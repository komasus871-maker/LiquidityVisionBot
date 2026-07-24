from uuid import uuid4

from services.copy_execution_journal import CopyExecutionJournal, JournalStatus
from services.copy_execution_planner import CopyExecutionPlanner
from services.execution_models import RiskProfile
from services.execution_queue import ExecutionQueueService


def _signal(signal_id: int | None = None):
    signal_id = signal_id or (9_950_000 + (uuid4().int % 900_000))
    return {
        "id": signal_id, "symbol": "BTCUSDT", "timeframe": "1H", "side": "LONG",
        "status": "ACTIVE", "entry": 100.0, "current_price": 100.0, "stop": 99.0,
        "tp1": 102.0, "tp2": 104.0, "tp3": 106.0, "confidence": 90.0,
    }


def _plan(signal_id: int | None = None):
    return CopyExecutionPlanner().build(
        telegram_id=9_950_000 + (uuid4().int % 900_000), signal=_signal(signal_id),
        profile=RiskProfile(max_notional_pct=100), balance=10_000,
    )


def test_queue_is_idempotent_and_reconstructs_plan():
    queue = ExecutionQueueService()
    plan = _plan()
    first = queue.enqueue(plan)
    second = queue.enqueue(plan)
    assert first.created is True
    assert second.created is False
    restored = queue.plan_from_row(first.row)
    assert restored == plan


def test_queue_drain_executes_planned_rows():
    queue = ExecutionQueueService()
    plan = _plan()
    queue.enqueue(plan)
    results = queue.drain(limit=10)
    match = [item for item in results if item.idempotency_key == plan.idempotency_key]
    assert match and match[0].status is JournalStatus.EXECUTED
    assert CopyExecutionJournal().get(plan.idempotency_key)["status"] == "EXECUTED"


def test_queue_summary_is_user_scoped():
    queue = ExecutionQueueService()
    plan = _plan()
    queue.enqueue(plan)
    counts = queue.summary(plan.telegram_id)
    assert counts["TOTAL"] >= 1
    assert counts["PLANNED"] >= 1
