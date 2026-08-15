from __future__ import annotations

from database.database import create_tables
from services.copy_execution_engine import CopyExecutionEngine
from services.copy_execution_journal import CopyExecutionJournal
from services.copy_execution_planner import CopyExecutionPlanner
from services.execution_inspection import ExecutionInspectionService
from services.execution_models import RiskProfile
from version import APP_VERSION, RELEASE_NAME


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr("database.database.DATA_DIR", tmp_path)
    monkeypatch.setattr("database.database.DATABASE_NAME", tmp_path / "database.db")
    create_tables()


def _plan(telegram_id: int = 99550):
    signal = {
        "id": 995501,
        "symbol": "BTCUSDT",
        "timeframe": "1H",
        "side": "LONG",
        "status": "ACTIVE",
        "entry": 100.0,
        "current_price": 100.0,
        "stop": 98.0,
        "tp1": 104.0,
        "tp2": 106.0,
        "tp3": 108.0,
        "confidence": 90.0,
    }
    return CopyExecutionPlanner().build(
        telegram_id=telegram_id,
        signal=signal,
        profile=RiskProfile(max_notional_pct=100),
        balance=10_000,
    )


def test_release_identity():
    assert APP_VERSION == "10.1.0"
    assert RELEASE_NAME == "Intelligence Product Platform"


def test_execution_records_auditable_timeline(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    plan = _plan()
    result = CopyExecutionEngine().execute(plan)
    assert result.status.value == "EXECUTED"

    inspection = ExecutionInspectionService().get(plan.telegram_id, plan.idempotency_key)
    assert inspection is not None
    statuses = [event["to_status"] for event in reversed(inspection.timeline)]
    assert statuses == ["PLANNED", "EXECUTING", "EXECUTED"]
    assert [event["actor"] for event in reversed(inspection.timeline)] == ["planner", "worker", "engine"]


def test_inspection_is_user_scoped_and_supports_multiple_references(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    plan = _plan()
    CopyExecutionEngine().execute(plan)
    service = ExecutionInspectionService()

    assert service.get(plan.telegram_id, str(plan.signal_id)) is not None
    assert service.get(plan.telegram_id, plan.plan_id) is not None
    assert service.get(plan.telegram_id + 1, plan.idempotency_key) is None
    assert service.fills(plan.telegram_id)[0]["to_status"] == "EXECUTED"


def test_same_state_replay_does_not_duplicate_transition_event(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    plan = _plan()
    engine = CopyExecutionEngine()
    engine.execute(plan)
    engine.execute(plan)
    events = CopyExecutionJournal().transition_events(
        telegram_id=plan.telegram_id,
        idempotency_key=plan.idempotency_key,
    )
    assert [event["to_status"] for event in events].count("EXECUTED") == 1
