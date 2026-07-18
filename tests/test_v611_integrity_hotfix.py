from __future__ import annotations

import importlib

import numpy as np
import pandas as pd

from services.trade_intelligence import TradeIntelligenceEngine


def _frame(up: bool) -> pd.DataFrame:
    close = np.linspace(100, 103 if up else 97, 20)
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame({
        "open": open_,
        "high": np.maximum(open_, close) + 0.3,
        "low": np.minimum(open_, close) - 0.3,
        "close": close,
        "volume": np.linspace(100, 180, 20),
    })


def _signal() -> dict:
    return {
        "side": "LONG",
        "entry": 100.0,
        "stop": 95.0,
        "effective_stop": 95.0,
        "confidence": 70.0,
        "dynamic_confidence": 70.0,
        "health_score": 66.0,
        "trade_health": "🟡 STABLE",
        "max_profit_pct": 1.0,
        "features_json": '{"direction_breakdown":{"Trend":12,"Structure":8,"Liquidity/SMC":4,"Momentum":6}}',
    }


def test_confidence_is_smoothed_between_cycles():
    engine = TradeIntelligenceEngine()
    first = engine.evaluate(_signal(), 102.0, _frame(True))
    state = _signal()
    state.update(dynamic_confidence=first.confidence, health_score=first.health_score, trade_health=first.health)
    second = engine.evaluate(state, 99.0, _frame(False))
    assert abs(second.confidence - first.confidence) < 20


def test_manual_stop_does_not_count_as_stop_loss(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("REQUIRE_PERSISTENT_DB", "false")

    import database.database as db
    import database.signal_history as sh
    importlib.reload(db)
    importlib.reload(sh)
    db.create_tables()
    repo = sh.SignalHistory()
    signal_id = repo.save({
        "owner_telegram_id": 7, "notification_chat_id": 7, "symbol": "BTC", "timeframe": "1h",
        "side": "LONG", "status": "WATCHING", "entry": 100.0, "preferred_entry_low": 99.0,
        "preferred_entry_high": 100.0, "stop": 95.0, "tp1": 105.0, "tp2": 110.0,
        "tp3": 115.0, "rr": 3.0, "confidence": 70.0, "bull_score": 70.0,
        "bear_score": 30.0, "recommendation": "BUY", "setup_key": "x", "features": {}, "reasons": [],
    })
    closed = repo.manual_stop(signal_id, owner_telegram_id=7)
    assert closed["status"] == "INVALIDATED"
    assert closed["result"] == "MANUAL_CANCEL"
    stats = repo.get_stats(7)
    assert stats["stop_hits"] == 0
    assert stats["invalidated_count"] == 1
