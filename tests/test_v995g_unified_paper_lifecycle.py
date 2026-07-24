from __future__ import annotations

import importlib
from pathlib import Path


def _fresh(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import database.database as db
    import services.paper_execution_lifecycle as lifecycle
    import services.copy_execution_journal as journal
    import services.copy_execution_engine as engine
    importlib.reload(db)
    db.create_tables()
    importlib.reload(lifecycle)
    importlib.reload(journal)
    importlib.reload(engine)
    return db, lifecycle, engine


def _plan():
    from services.execution_models import CopyExecutionPlan, ExecutionPlanStatus
    return CopyExecutionPlan(
        plan_id="plan-v995g", idempotency_key="key-v995g", status=ExecutionPlanStatus.APPROVED,
        code="APPROVED", reason="ok", telegram_id=100, signal_id=200,
        exchange_account_id=None, symbol="BTC", timeframe="1h", side="LONG",
        order_type="MARKET", entry_price=65000.0, quantity=0.01, notional=650.0,
        leverage=5, stop_loss=64000.0, take_profits=(66000.0,), expected_slippage_pct=0.12,
    )


def test_engine_creates_order_fill_position(tmp_path, monkeypatch):
    _, lifecycle, engine = _fresh(tmp_path, monkeypatch)
    result = engine.CopyExecutionEngine().execute(_plan())
    assert result.status.value == "EXECUTED"
    service = lifecycle.PaperExecutionLifecycle()
    orders = service.recent_orders(100)
    fills = service.recent_fills(100)
    positions = service.recent_positions(100)
    assert orders[0]["status"] == "FILLED"
    assert float(orders[0]["filled_quantity"]) == 0.01
    assert float(fills[0]["price"]) == 65000.0
    assert float(fills[0]["commission"]) > 0
    assert positions[0]["status"] == "OPEN"
    assert float(positions[0]["average_entry"]) == 65000.0


def test_replay_has_exactly_one_fill(tmp_path, monkeypatch):
    _, lifecycle, engine = _fresh(tmp_path, monkeypatch)
    runner = engine.CopyExecutionEngine()
    runner.execute(_plan())
    runner.execute(_plan())
    service = lifecycle.PaperExecutionLifecycle()
    assert len(service.recent_orders(100)) == 1
    assert len(service.recent_fills(100)) == 1
    assert len(service.recent_positions(100)) == 1


def test_partial_fill_state_machine(tmp_path, monkeypatch):
    _, lifecycle, _ = _fresh(tmp_path, monkeypatch)
    service = lifecycle.PaperExecutionLifecycle()
    plan = _plan()
    order, _ = service.submit(plan, execution_ref="paper:test")
    service.transition(order["id"], lifecycle.OrderStatus.ACCEPTED)
    first = service.record_fill(order["id"], quantity=0.004, price=64990.0, fill_key="partial-1")
    assert first.order["status"] == "PARTIALLY_FILLED"
    second = service.record_fill(order["id"], quantity=0.006, price=65010.0, fill_key="partial-2")
    assert second.order["status"] == "FILLED"
    assert abs(float(second.order["average_fill_price"]) - 65002.0) < 1e-9
    assert len(service.recent_fills(100)) == 2


def test_invalid_terminal_transition(tmp_path, monkeypatch):
    _, lifecycle, _ = _fresh(tmp_path, monkeypatch)
    service = lifecycle.PaperExecutionLifecycle()
    plan = _plan()
    result = service.execute_market(plan, fill_price=65000.0, execution_ref="paper:test")
    try:
        service.transition(result.order["id"], lifecycle.OrderStatus.CANCELLED)
    except lifecycle.InvalidOrderTransition:
        pass
    else:
        raise AssertionError("FILLED order must be terminal")
