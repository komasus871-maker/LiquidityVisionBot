from __future__ import annotations

from dataclasses import replace

from database.database import create_tables
from services.copy_execution_engine import CopyExecutionEngine
from services.copy_execution_journal import JournalStatus
from services.copy_execution_planner import CopyExecutionPlanner
from services.execution_adapter import ExecutionAdapterResult
from services.execution_models import ExecutionMode, RiskProfile
from services.execution_validation_pipeline import (
    ExecutionValidationPipeline,
    OrderPayloadValidator,
    PaperSafetyValidator,
    PlanIdentityValidator,
)
from version import APP_VERSION, RELEASE_NAME


def _db(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("database.database.DATA_DIR", tmp_path)
    monkeypatch.setattr("database.database.DATABASE_NAME", tmp_path / "database.db")
    create_tables()


def _plan():
    signal = {
        "id": 9954,
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "side": "LONG",
        "status": "ACTIVE",
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
    return CopyExecutionPlanner().build(
        telegram_id=54,
        signal=signal,
        profile=RiskProfile(max_notional_pct=100),
        balance=10_000,
    )


def test_release_identity() -> None:
    assert tuple(int(part) if part.isdigit() else part for part in APP_VERSION.replace("e", ".5").split("."))
    assert RELEASE_NAME


def test_default_pipeline_accepts_valid_paper_plan() -> None:
    result = ExecutionValidationPipeline().validate(
        _plan(),
        mode=ExecutionMode.PAPER,
        adapter_mode=ExecutionMode.PAPER,
    )
    assert result.allowed is True
    assert result.code == "VALIDATED"
    assert result.failures == ()


def test_pipeline_collects_contract_failures() -> None:
    plan = replace(_plan(), symbol="", quantity=0.0, stop_loss=None)
    result = ExecutionValidationPipeline().validate(
        plan,
        mode=ExecutionMode.PAPER,
        adapter_mode=ExecutionMode.PAPER,
    )
    assert result.allowed is False
    assert result.code == "MISSING_SYMBOL"
    assert {failure.validator for failure in result.failures} == {"plan_identity", "order_payload"}


def test_engine_rejects_invalid_plan_before_adapter_claim(tmp_path, monkeypatch) -> None:
    _db(tmp_path, monkeypatch)

    class CountingAdapter:
        mode = ExecutionMode.PAPER

        def __init__(self) -> None:
            self.calls = 0

        def execute(self, plan):
            self.calls += 1
            return ExecutionAdapterResult(True, execution_ref="should-not-run")

    adapter = CountingAdapter()
    invalid = replace(_plan(), quantity=0.0)
    result = CopyExecutionEngine(adapter=adapter).execute(invalid)

    assert result.status is JournalStatus.REJECTED
    assert result.code == "INVALID_QUANTITY"
    assert result.claimed is False
    assert adapter.calls == 0


def test_live_and_adapter_mode_are_fail_closed_by_pipeline(tmp_path, monkeypatch) -> None:
    _db(tmp_path, monkeypatch)

    class LiveAdapter:
        mode = ExecutionMode.LIVE

        def execute(self, plan):
            raise AssertionError("live adapter must never be invoked")

    result = CopyExecutionEngine(adapter=LiveAdapter(), mode=ExecutionMode.LIVE).execute(_plan())
    assert result.status is JournalStatus.REJECTED
    assert result.code == "LIVE_DISABLED"
    assert result.claimed is False


def test_pipeline_supports_explicit_validator_composition() -> None:
    pipeline = ExecutionValidationPipeline((
        PlanIdentityValidator(),
        OrderPayloadValidator(),
        PaperSafetyValidator(),
    ))
    result = pipeline.validate(
        _plan(),
        mode=ExecutionMode.PAPER,
        adapter_mode=ExecutionMode.PAPER,
    )
    assert result.allowed is True
