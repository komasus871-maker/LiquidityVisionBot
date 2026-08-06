from __future__ import annotations

from datetime import datetime, timezone

from database.database import connect, create_tables
from database.database import DBRow
from services.execution_models import CopyExecutionPlan, ExecutionPlanStatus, RiskProfile
from services.execution_portfolio import ExecutionPortfolioEngine
from services.execution_validator import ExecutionValidator
from services.paper_execution_lifecycle import PaperExecutionLifecycle
from services.copy_trading import CopyTradingService


def setup_db(tmp_path, monkeypatch):
    monkeypatch.setattr("database.database.DATA_DIR", tmp_path)
    monkeypatch.setattr("database.database.DATABASE_NAME", tmp_path / "v998.db")
    create_tables()
    create_tables()


def plan(user=1, signal=1, symbol="BTCUSDT", side="LONG"):
    return CopyExecutionPlan(
        plan_id=f"p-{user}-{signal}", idempotency_key=f"k-{user}-{signal}",
        status=ExecutionPlanStatus.APPROVED, code="APPROVED", reason="ok",
        telegram_id=user, signal_id=signal, exchange_account_id=None, symbol=symbol,
        timeframe="1h", side=side, order_type="MARKET", entry_price=100,
        quantity=10, notional=1000, stop_loss=90 if side == "LONG" else 110,
        risk_amount=100, take_profits=(110, 120, 130),
    )


def open_position(service, item):
    order, _ = service.submit(item)
    service.transition(order["id"], "ACCEPTED")
    return service.record_fill(order["id"], quantity=10, price=100, fill_key=f"f-{item.signal_id}").position


def test_fresh_repeated_schema_and_unified_open_accounting(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    service = PaperExecutionLifecycle()
    open_position(service, plan())
    snap = ExecutionPortfolioEngine().snapshot(1)
    assert snap.open_positions == 1 and snap.confirmed_heat_r == 1
    assert snap.risk_complete == 1 and snap.resolved
    assert snap.gross_notional == 1000 and snap.net_notional == 1000
    assert snap.net_equity == 9999.5  # entry commission exactly once


def test_partial_close_heat_pnl_commission_r_and_replay(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    service = PaperExecutionLifecycle()
    position = open_position(service, plan())
    first = service.apply_signal_transition(position["id"], signal_status="TP1", price=110,
                                            event_key="signal:1:TP1", commission_rate=.0004)
    replay = service.apply_signal_transition(position["id"], signal_status="TP1", price=110,
                                             event_key="signal:1:TP1", commission_rate=.0004)
    snap = ExecutionPortfolioEngine().snapshot(1)
    assert first.applied and not replay.applied
    assert snap.open_positions == 1 and snap.risk_partial == 1 and snap.confirmed_heat_r == .5
    assert snap.realized_gross_pnl == 50 and snap.realized_r == .5
    assert snap.commissions == .72 and snap.net_equity == 10099.28
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM paper_portfolio_ledger").fetchone()[0] == 3


def test_closed_position_retains_realized_and_cooldown(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    service = PaperExecutionLifecycle()
    position = open_position(service, plan())
    service.close_position(position["id"], quantity=10, exit_price=95, event_key="manual:close")
    snap = ExecutionPortfolioEngine().snapshot(1, cooldown_min=30)
    assert snap.open_positions == 0 and snap.confirmed_heat_r == 0
    assert snap.realized_gross_pnl == -50 and snap.realized_r == -.5
    assert "BTCUSDT" in snap.cooldown_symbols and snap.daily_realized_result < -50


def test_missing_and_invalid_risk_fail_closed(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        for key, signal, risk in (("missing", 1, None), ("invalid", 2, -1)):
            conn.execute(
                """INSERT INTO paper_execution_positions(
                    position_key,order_id,idempotency_key,telegram_id,signal_id,symbol,timeframe,side,status,
                    quantity,initial_quantity,average_entry,last_price,stop_loss,initial_risk_amount,created_at,updated_at)
                    VALUES(?,?,?,?,?,'BTCUSDT','1h','LONG','OPEN',1,1,100,100,90,?,?,?)""",
                (key, signal, key, 1, signal, risk, now, now),
            )
    snap = ExecutionPortfolioEngine().snapshot(1)
    assert not snap.resolved and snap.risk_missing == 1 and snap.risk_invalid == 1


def test_long_short_exposure_rejections_and_multi_user_isolation(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    service = PaperExecutionLifecycle()
    open_position(service, plan(1, 1, "BTCUSDT", "LONG"))
    open_position(service, plan(1, 2, "ETHUSDT", "SHORT"))
    open_position(service, plan(2, 3, "SOLUSDT", "LONG"))
    snap = ExecutionPortfolioEngine().snapshot(1)
    assert snap.open_positions == 2 and snap.gross_notional == 2000 and snap.net_notional == 0
    assert ExecutionPortfolioEngine().snapshot(2).open_positions == 1


def test_validator_unresolved_prevents_admission(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    from services.execution_models import PortfolioState
    decision = ExecutionValidator().validate(
        signal={"status":"ACTIVE", "side":"LONG", "entry":100, "stop":90,
                "tp1":110, "tp2":120, "tp3":130},
        profile=RiskProfile(), balance=10_000,
        portfolio=PortfolioState(portfolio_state_resolved=False,
                                 unified_unresolved_risk_positions=1),
    )
    assert not decision.allowed and decision.code == "PORTFOLIO_STATE_UNRESOLVED"


def test_parity_and_source_mode_rollback(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    open_position(PaperExecutionLifecycle(), plan())
    parity = ExecutionPortfolioEngine().parity_report(1)
    assert parity["status"] == "MISMATCH" and "open_count" in parity["mismatches"]
    monkeypatch.setenv("PORTFOLIO_ACCOUNTING_SOURCE", "UNIFIED")
    assert CopyTradingService()._portfolio_state(1, "ETHUSDT", 30).position_state_source == "UNIFIED"
    monkeypatch.setenv("PORTFOLIO_ACCOUNTING_SOURCE", "LEGACY")
    legacy = CopyTradingService()._portfolio_state(1, "ETHUSDT", 30)
    assert legacy.position_state_source == "LEGACY_ROLLBACK" and legacy.open_positions == 0


def test_parity_aggregate_is_postgres_real_dict_safe(monkeypatch):
    """RealDictCursor collapses unnamed duplicate COALESCE columns to one key."""
    from contextlib import contextmanager
    import services.execution_portfolio as portfolio_module

    class FakeConnection:
        def execute(self, sql, params=()):
            class Cursor:
                def fetchone(inner_self):
                    if "FROM paper_positions" in sql and "SUM" in sql:
                        assert "AS legacy_open_count" in sql
                        assert "AS legacy_heat_r" in sql
                        assert "AS legacy_realized_pnl" in sql
                        assert "AS legacy_rejection_count" in sql
                        return DBRow(legacy_open_count=1, legacy_heat_r=.5,
                                     legacy_realized_pnl=25, legacy_rejection_count=2)
                    if "FROM execution_events" in sql:
                        return DBRow(daily_pnl=5)
                    raise AssertionError(sql)

                def fetchall(inner_self):
                    return []
            return Cursor()

    @contextmanager
    def fake_connect():
        yield FakeConnection()

    engine = ExecutionPortfolioEngine()
    monkeypatch.setattr(engine, "snapshot", lambda *args, **kwargs: type("Snapshot", (), {
        "starting_balance": 10_000.0, "open_positions": 1, "confirmed_heat_r": .5,
        "realized_gross_pnl": 25.0, "daily_realized_result": 5.0,
        "net_equity": 10_025.0, "symbols": (), "cooldown_symbols": (),
        "rejection_count": 2, "resolved": True,
    })())
    monkeypatch.setattr(portfolio_module, "connect", fake_connect)
    report = engine.parity_report(1)
    assert report["status"] == "MATCH"
