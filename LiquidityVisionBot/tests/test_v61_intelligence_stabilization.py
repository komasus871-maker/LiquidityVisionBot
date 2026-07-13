import json
from pathlib import Path

from database import database as db
from database.signal_history import SignalHistory


def _reset(tmp_path: Path):
    db.USE_POSTGRES = False
    db.DATA_DIR = tmp_path
    db.DATABASE_NAME = tmp_path / "database.db"
    db.create_tables()


def test_intelligence_snapshot_roundtrip(tmp_path):
    _reset(tmp_path)
    history = SignalHistory()
    signal_id = history.save({
        "owner_telegram_id": 1,
        "notification_chat_id": 1,
        "symbol": "BTC",
        "timeframe": "1h",
        "side": "LONG",
        "status": "ACTIVE",
        "entry": 100.0,
        "preferred_entry_low": 99.0,
        "preferred_entry_high": 101.0,
        "stop": 95.0,
        "tp1": 105.0,
        "tp2": 110.0,
        "tp3": 115.0,
        "rr": 3.0,
        "confidence": 60.0,
        "bull_score": 60.0,
        "bear_score": 20.0,
        "recommendation": "BUY",
        "setup_key": "test",
        "features": {},
        "reasons": [],
    })
    snapshot = {
        "created_at": "2026-01-01T00:00:00+00:00",
        "confidence": 72.0,
        "confidence_delta": 8.0,
        "health": "🟡 STABLE",
        "health_score": 68.0,
        "trend": 80.0,
        "structure": 70.0,
        "liquidity": 55.0,
        "momentum": 60.0,
        "risk_used": 20.0,
        "distance_to_stop": 80.0,
        "mfe_giveback": 0.1,
        "commentary": "Valid.",
        "alert_reasons": ["Confidence changed"],
        "component_deltas": {"trend": 5.0},
    }
    history.save_intelligence_snapshot(signal_id, snapshot)
    loaded = history.get_latest_intelligence_snapshot(signal_id)
    assert loaded["confidence"] == 72.0
    assert loaded["health_score"] == 68.0
    assert history.get_intelligence_timeline(signal_id)[0]["confidence"] == 72.0


def test_closed_trade_creates_learning_sample(tmp_path):
    _reset(tmp_path)
    history = SignalHistory()
    signal_id = history.save({
        "owner_telegram_id": 1,
        "notification_chat_id": 1,
        "symbol": "ETH",
        "timeframe": "1h",
        "side": "SHORT",
        "status": "WATCHING",
        "entry": 100.0,
        "preferred_entry_low": 99.0,
        "preferred_entry_high": 101.0,
        "stop": 105.0,
        "tp1": 95.0,
        "tp2": 90.0,
        "tp3": 85.0,
        "rr": 3.0,
        "confidence": 60.0,
        "bull_score": 20.0,
        "bear_score": 60.0,
        "recommendation": "SELL",
        "setup_key": "test2",
        "features": {"direction_breakdown": {"Trend": 10}},
        "reasons": [],
    })
    history.update_lifecycle(
        signal_id,
        status="STOP",
        closed_at="2026-01-01T01:00:00+00:00",
        activated_at="2026-01-01T00:00:00+00:00",
        realized_r=-1.0,
        result="STOP",
        max_profit_pct=0.5,
        max_drawdown_pct=-1.0,
    )
    history.save_learning_sample(signal_id)
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM learning_samples WHERE signal_id=?", (signal_id,)).fetchone()
    assert row is not None
    assert row["result"] == "STOP"
    assert row["duration_minutes"] == 60
