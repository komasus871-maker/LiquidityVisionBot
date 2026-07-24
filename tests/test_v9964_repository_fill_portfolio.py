from __future__ import annotations

import json

from database.database import create_tables
from services.copy_execution_engine import CopyExecutionEngine
from services.execution_event_bus import ExecutionEvent, ExecutionEventBus
from services.execution_models import CopyExecutionPlan, ExecutionPlanStatus
from services.execution_portfolio import ExecutionPortfolioEngine
from services.execution_repositories import ExecutionRepository


def make_plan(key: str = "idem-9964") -> CopyExecutionPlan:
    return CopyExecutionPlan(
        plan_id=f"plan-{key}", idempotency_key=key,
        status=ExecutionPlanStatus.APPROVED, code="APPROVED", reason="ok",
        telegram_id=9964, signal_id=64, exchange_account_id=None,
        symbol="BTC", timeframe="1h", side="LONG", order_type="MARKET",
        entry_price=100.0, quantity=2.0, notional=200.0, leverage=2,
    )


def test_repository_fill_position_and_portfolio_pipeline(tmp_path, monkeypatch):
    monkeypatch.setattr("database.database.DATA_DIR", tmp_path)
    monkeypatch.setattr("database.database.DATABASE_NAME", tmp_path / "test.db")
    create_tables()

    result = CopyExecutionEngine().execute(make_plan())
    assert result.status.value == "EXECUTED"

    repo = ExecutionRepository()
    order = repo.order_by_idempotency("idem-9964")
    assert order and order["status"] == "FILLED"
    fills = repo.fills_for_order(int(order["id"]))
    assert len(fills) == 1
    assert fills[0]["quantity"] == 2.0
    position = repo.position_by_idempotency("idem-9964")
    assert position and position["status"] == "OPEN"

    snapshot = ExecutionPortfolioEngine(repo).snapshot(9964)
    assert snapshot.open_positions == 1
    assert snapshot.gross_notional == 200.0
    assert snapshot.net_notional == 200.0

    events = ExecutionEventBus().recent(9964, event_type="COPY_EXECUTION_POSITIONED")
    assert len(events) == 1
    assert events[0]["details"]["idempotency_key"] == "idem-9964"


def test_execution_event_bus_is_durable_and_subscribers_are_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr("database.database.DATA_DIR", tmp_path)
    monkeypatch.setattr("database.database.DATABASE_NAME", tmp_path / "events.db")
    create_tables()
    seen = []
    bus = ExecutionEventBus()
    bus.subscribe(lambda event: seen.append(event.event_type))
    bus.subscribe(lambda event: (_ for _ in ()).throw(RuntimeError("observer failure")))
    event_id = bus.publish(ExecutionEvent(7, 8, "TEST_EVENT", {"ok": True}))
    assert event_id > 0
    assert seen == ["TEST_EVENT"]
    rows = bus.recent(7)
    assert rows[0]["event_type"] == "TEST_EVENT"
    assert json.loads(rows[0]["details_json"])["ok"] is True
