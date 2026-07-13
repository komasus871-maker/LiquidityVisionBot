from __future__ import annotations

import json

import numpy as np
import pandas as pd

from services.trade_intelligence import TradeIntelligenceEngine


def frame(up: bool = True) -> pd.DataFrame:
    close = np.linspace(100, 103 if up else 97, 20)
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame({
        "open": open_,
        "high": np.maximum(open_, close) + 0.3,
        "low": np.minimum(open_, close) - 0.3,
        "close": close,
        "volume": np.linspace(100, 180, 20),
    })


def signal(side: str = "LONG") -> dict:
    return {
        "side": side,
        "entry": 100.0,
        "stop": 95.0 if side == "LONG" else 105.0,
        "effective_stop": 95.0 if side == "LONG" else 105.0,
        "confidence": 65.0,
        "max_profit_pct": 1.0,
        "features_json": json.dumps({
            "direction_breakdown": {
                "Trend": 12,
                "Structure": 8,
                "Liquidity/SMC": 4,
                "Momentum": 6,
            }
        }),
    }


def test_dynamic_components_are_not_static_fifties():
    snap = TradeIntelligenceEngine().evaluate(signal(), 102.5, frame(True))
    values = {snap.trend, snap.structure, snap.liquidity, snap.momentum}
    assert len(values) > 1
    assert snap.confidence > 50
    assert snap.health_score > 0
    assert snap.commentary


def test_risk_crossing_generates_smart_alert_reason():
    item = signal()
    item["last_risk_used"] = 60
    item["dynamic_confidence"] = 75
    snap = TradeIntelligenceEngine().evaluate(item, 96.0, frame(False))
    assert snap.risk_used >= 75
    assert any("Risk used crossed 75%" in reason for reason in snap.alert_reasons)


def test_health_deteriorates_near_stop():
    healthy = TradeIntelligenceEngine().evaluate(signal(), 102.0, frame(True))
    weak = TradeIntelligenceEngine().evaluate(signal(), 95.5, frame(False))
    assert weak.health_score < healthy.health_score
    assert weak.distance_to_stop < healthy.distance_to_stop
