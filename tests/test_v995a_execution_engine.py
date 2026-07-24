from __future__ import annotations

from database.database import create_tables
from services.copy_execution_engine import CopyExecutionEngine
from services.copy_execution_journal import JournalStatus
from services.copy_execution_planner import CopyExecutionPlanner
from services.execution_adapter import ExecutionAdapterResult
from services.execution_models import ExecutionMode, RiskProfile


def _signal(status: str = "ACTIVE") -> dict:
    return {
        "id": 995,
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "side": "LONG",
        "status": status,
        "entry": 100.0,
        "current_price": 100.0,
        "stop": 98.0,
        "tp1": 104.0,
        "tp2": 106.0,
        "tp3": 108.0,
        "confidence": 80.0,
        "preferred_entry_low": 99.0,
        "preferred_entry_high": 101.0,
    }


def _db(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("database.database.DATA_DIR", tmp_path)
    monkeypatch.setattr("database.database.DATABASE_NAME", tmp_path / "database.db")
    create_tables()


def _plan(**kwargs):
    return CopyExecutionPlanner().build(
        telegram_id=42,
        signal=_signal(),
        profile=RiskProfile(max_notional_pct=100),
        balance=10_000,
        **kwargs,
    )


def test_execution_engine_keeps_terminal_status_contract() -> None:
    assert JournalStatus.EXECUTED in CopyExecutionEngine.TERMINAL
    assert JournalStatus.FAILED in CopyExecutionEngine.TERMINAL
    assert JournalStatus.CANCELLED in CopyExecutionEngine.TERMINAL


def test_engine_executes_approved_plan_once(tmp_path, monkeypatch) -> None:
    _db(tmp_path, monkeypatch)
    plan = _plan()
    engine = CopyExecutionEngine()

    first = engine.execute(plan)
    second = engine.execute(plan)

    assert first.status is JournalStatus.EXECUTED
    assert first.created is True and first.claimed is True
    assert first.execution_ref and first.execution_ref.startswith("paper:")
    assert second.status is JournalStatus.EXECUTED
    assert second.created is False and second.claimed is False
    assert second.code == "IDEMPOTENT_REPLAY"
    assert second.execution_ref == first.execution_ref


def test_rejected_plan_is_persisted_without_adapter_execution(tmp_path, monkeypatch) -> None:
    _db(tmp_path, monkeypatch)
    plan = CopyExecutionPlanner().build(
        telegram_id=42,
        signal=_signal(status="WATCHING"),
        profile=RiskProfile(max_notional_pct=100),
        balance=10_000,
    )
    result = CopyExecutionEngine().execute(plan)
    assert result.status is JournalStatus.REJECTED
    assert result.claimed is False
    assert result.code == "SIGNAL_NOT_ACTIVE"


def test_adapter_exception_transitions_to_failed(tmp_path, monkeypatch) -> None:
    _db(tmp_path, monkeypatch)

    class BrokenAdapter:
        mode = ExecutionMode.PAPER

        def execute(self, plan):
            raise RuntimeError("boom")

    result = CopyExecutionEngine(adapter=BrokenAdapter()).execute(_plan())
    assert result.status is JournalStatus.FAILED
    assert result.code == "ADAPTER_EXCEPTION"
    assert "RuntimeError: boom" in result.reason


def test_live_mode_remains_fail_closed(tmp_path, monkeypatch) -> None:
    _db(tmp_path, monkeypatch)

    class LiveAdapter:
        mode = ExecutionMode.LIVE

        def execute(self, plan):
            return ExecutionAdapterResult(True, execution_ref="live:forbidden")

    result = CopyExecutionEngine(adapter=LiveAdapter(), mode=ExecutionMode.LIVE).execute(_plan())
    assert result.status is JournalStatus.FAILED
    assert result.code == "LIVE_DISABLED"
    assert result.execution_ref is None
