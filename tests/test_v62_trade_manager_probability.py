import importlib
import json
from datetime import datetime, timezone


def _reload_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("REQUIRE_PERSISTENT_DB", "false")
    import database.database as db
    import database.signal_history as sh
    import database.candidate_history as ch
    import services.trade_manager as tm
    importlib.reload(db)
    importlib.reload(sh)
    importlib.reload(ch)
    importlib.reload(tm)
    db.create_tables()
    return db, sh, ch, tm


def _payload(side, status="ACTIVE", entry=100.0):
    return {
        "owner_telegram_id": 1,
        "notification_chat_id": 1,
        "symbol": "BTC",
        "timeframe": "1h",
        "side": side,
        "status": status,
        "entry": entry,
        "preferred_entry_low": entry - 1,
        "preferred_entry_high": entry + 1,
        "stop": entry - 5 if side == "LONG" else entry + 5,
        "tp1": entry + 5 if side == "LONG" else entry - 5,
        "tp2": entry + 10 if side == "LONG" else entry - 10,
        "tp3": entry + 15 if side == "LONG" else entry - 15,
        "rr": 3.0,
        "confidence": 70.0,
        "bull_score": 70.0 if side == "LONG" else 20.0,
        "bear_score": 70.0 if side == "SHORT" else 20.0,
        "recommendation": "BUY" if side == "LONG" else "SELL",
        "setup_key": "test",
        "features": {"trend": side},
        "reasons": [],
    }


def test_trade_manager_reconciles_opposite_active_and_creates_candidate(tmp_path, monkeypatch):
    db, sh, ch, tm = _reload_db(tmp_path, monkeypatch)
    history = sh.SignalHistory()
    # Temporarily remove the unique index to simulate legacy/race-created rows.
    with db.connect() as conn:
        conn.execute("DROP INDEX IF EXISTS idx_one_open_market_plan")
    long_id = history.save(_payload("LONG"))
    short_id = history.save(_payload("SHORT", entry=101.0))
    now = datetime.now(timezone.utc).isoformat()
    history.update_lifecycle(long_id, activated_at=now)
    history.update_lifecycle(short_id, activated_at=now)

    manager = tm.TradeManager()
    keeper = manager.reconcile_market(1, "BTC", "1h")
    rows = history.get_open_market(1, "BTC", "1h")
    assert len(rows) == 1
    assert keeper["id"] == rows[0]["id"]
    loser_id = short_id if rows[0]["id"] == long_id else long_id
    assert history.get_by_id(loser_id)["status"] == "INVALIDATED"
    candidates = ch.CandidateHistory().recent(1)
    assert len(candidates) == 1
    assert candidates[0]["blocked_by_signal_id"] == rows[0]["id"]


def test_weighted_probability_reports_effective_sample_and_intervals():
    from services.probability_engine import ProbabilityEngine, SimilarCase
    engine = ProbabilityEngine()
    cases = [
        SimilarCase(1, "BTC", "1h", "LONG", "TP3", 90, True, True, True, False, 4.0, -1.0, 120, 3.0),
        SimilarCase(2, "ETH", "1h", "LONG", "STOP", 70, False, False, False, True, 0.5, -1.2, 60, -1.0),
        SimilarCase(3, "SOL", "1h", "LONG", "TP1", 50, True, False, False, False, 1.8, -0.7, 90, 1.0),
    ]
    stats = engine.weighted_stats(cases)
    assert stats["samples"] == 3
    assert 0 < stats["effective_samples"] <= 3
    assert 0 <= stats["tp1_low"] <= stats["tp1_rate"] <= stats["tp1_high"] <= 100
    assert 0 <= stats["stop_low"] <= stats["stop_rate"] <= stats["stop_high"] <= 100
    assert stats["estimated"] is False  # effective sample is below 3 after weighting
