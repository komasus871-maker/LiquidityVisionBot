from database.database import create_tables, connect
from services.copy_trading import CopyTradingService
from services.portfolio_reconciliation import PortfolioReconciliationService


def setup_db(tmp_path, monkeypatch):
    monkeypatch.setattr("database.database.DATA_DIR", tmp_path)
    monkeypatch.setattr("database.database.DATABASE_NAME", tmp_path / "reconciliation.db")
    create_tables()


def seed_signal_and_position(user_id: int, signal_status: str, position_status: str = "OPEN"):
    with connect() as conn:
        now = "2026-07-24T00:00:00+00:00"
        conn.execute("INSERT INTO users(telegram_id,username,created_at) VALUES(?,?,?)", (user_id, "u", now))
        conn.execute("""INSERT INTO signals(id,symbol,timeframe,side,status,entry,stop,tp1,tp2,tp3,rr,confidence,bull_score,bear_score,recommendation,setup_key,features_json,reasons_json,created_at,updated_at)
                      VALUES(1,'BTC','1h','LONG',?,100,90,110,120,130,2,70,70,30,'BUY','setup','{}','[]',?,?)""", (signal_status, now, now))
        conn.execute("""INSERT INTO paper_positions(telegram_id,signal_id,symbol,timeframe,side,status,entry_price,last_price,stop_price,initial_risk_r,remaining_fraction,created_at,updated_at)
                      VALUES(?,1,'BTC','1h','LONG',?,100,100,90,1,1,?,?)""", (user_id, position_status, now, now))


def test_terminal_legacy_position_is_removed_from_heat_idempotently(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    seed_signal_and_position(9966, "CLOSED")
    svc = PortfolioReconciliationService()
    first = svc.reconcile(9966)
    second = svc.reconcile(9966)
    assert first.stale_legacy_closed == 1
    assert first.legacy_open_after == 0
    assert second.stale_legacy_closed == 0
    assert second.legacy_open_after == 0
    state = CopyTradingService()._portfolio_state(9966, "ETH", 30)
    assert state.open_positions == 0
    assert state.current_heat_r == 0


def test_active_legacy_position_remains_fail_closed_and_is_reported(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    seed_signal_and_position(9967, "ACTIVE")
    service = CopyTradingService()
    state = service._portfolio_state(9967, "ETH", 30)
    stats = service.profile_stats(9967)
    assert state.open_positions == 1
    assert state.current_heat_r == 1
    assert stats["open_count"] == 1
    assert stats["reconciliation_unified_open"] == 0
    assert stats["reconciliation_status"] == "MISMATCH"
