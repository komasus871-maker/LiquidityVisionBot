from database.database import create_tables
from services.copy_execution_engine import CopyExecutionEngine
from services.execution_models import CopyExecutionPlan, ExecutionPlanStatus
from services.execution_portfolio import ExecutionPortfolioEngine
from services.paper_execution_lifecycle import PaperExecutionLifecycle
from services.execution_repositories import ExecutionRepository


def plan():
    return CopyExecutionPlan(plan_id="p-9965", idempotency_key="idem-9965", status=ExecutionPlanStatus.APPROVED, code="APPROVED", reason="ok", telegram_id=9965, signal_id=65, exchange_account_id=None, symbol="BTC", timeframe="1h", side="LONG", order_type="MARKET", entry_price=100.0, quantity=2.0, notional=200.0, leverage=2)

def test_mark_to_market_partial_and_full_close(tmp_path, monkeypatch):
    monkeypatch.setattr("database.database.DATA_DIR", tmp_path)
    monkeypatch.setattr("database.database.DATABASE_NAME", tmp_path / "lifecycle.db")
    create_tables()
    assert CopyExecutionEngine().execute(plan()).status.value == "EXECUTED"
    repo = ExecutionRepository()
    pos = repo.position_by_idempotency("idem-9965")
    lifecycle = PaperExecutionLifecycle()
    mtm = lifecycle.mark_to_market(int(pos["id"]), last_price=110.0)
    assert mtm["unrealized_pnl"] == 20.0
    partial = lifecycle.close_position(int(pos["id"]), quantity=0.5, exit_price=110.0, commission_rate=0)
    assert partial["status"] == "PARTIALLY_CLOSED"
    assert partial["quantity"] == 1.5
    assert partial["realized_pnl"] == 5.0
    closed = lifecycle.close_position(int(pos["id"]), quantity=99, exit_price=90.0, commission_rate=0)
    assert closed["status"] == "CLOSED"
    assert closed["quantity"] == 0.0
    assert closed["realized_pnl"] == -10.0
    snapshot = ExecutionPortfolioEngine(repo).snapshot(9965)
    assert snapshot.open_positions == 0
    history = repo.positions_for_user(9965)
    assert history[0]["status"] == "CLOSED"
