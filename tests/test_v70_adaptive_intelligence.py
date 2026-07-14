from __future__ import annotations

import json

import numpy as np
import pandas as pd

from services.trade_intelligence import TradeIntelligenceEngine
from services.probability_engine import ProbabilityEngine


def frame(up: bool = True) -> pd.DataFrame:
    close = np.linspace(100, 104 if up else 96, 20)
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame({
        "open": open_,
        "high": np.maximum(open_, close) + 0.2,
        "low": np.minimum(open_, close) - 0.2,
        "close": close,
        "volume": np.linspace(100, 170, 20),
    })


def base_signal() -> dict:
    return {
        "side": "LONG",
        "entry": 100.0,
        "stop": 95.0,
        "effective_stop": 95.0,
        "tp1": 105.0,
        "tp2": 110.0,
        "tp3": 115.0,
        "confidence": 55.0,
        "dynamic_confidence": 55.0,
        "max_profit_pct": 4.9,
        "features_json": json.dumps({
            "direction_breakdown": {
                "Trend": 8,
                "Structure": 8,
                "Liquidity/SMC": 2,
                "Momentum": -5,
            }
        }),
    }


def test_near_tp_trade_is_not_weakened_only_by_soft_momentum():
    snap = TradeIntelligenceEngine().evaluate(base_signal(), 104.9, frame(False))
    assert snap.target_progress >= 95
    assert snap.health_score >= 60
    assert snap.suggested_action in {"PROTECT PROFIT", "HOLD / MONITOR TP1"}


def test_probability_is_disabled_below_reliable_sample(monkeypatch):
    engine = ProbabilityEngine()
    monkeypatch.setattr(engine, "exact_stats", lambda *a, **k: {
        "samples": 2, "tp1_rate": 50.0, "tp2_rate": 0.0, "tp3_rate": 0.0,
        "stop_rate": 50.0, "reliability": "Insufficient", "sufficient": False,
        "avg_mfe": 1.0, "avg_mae": -1.0,
    })
    monkeypatch.setattr(engine, "similar_cases", lambda **k: [])
    monkeypatch.setattr(engine, "similar_stats", lambda cases: {
        "samples": 2, "tp1_rate": 50.0, "tp2_rate": 0.0, "tp3_rate": 0.0,
        "stop_rate": 50.0, "reliability": "Insufficient", "sufficient": False,
        "avg_mfe": 1.0, "avg_mae": -1.0,
    })
    result = engine.live_context({"setup_key": "x", "timeframe": "1h", "side": "LONG"})
    assert result["sufficient"] is False
    assert result["tp1_rate"] == 0.0
    assert "Need at least" in result["disabled_reason"]
