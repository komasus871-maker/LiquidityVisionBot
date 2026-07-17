from services.conviction_engine import ConvictionEngine
from services.decision_quality import DecisionQualityEngine


def base_analysis():
    return {
        "direction": "LONG", "direction_score": 78, "confidence": 78,
        "setup_score": 76, "score": 76, "execution_readiness": 74,
        "entry_quality": 72, "risk_quality": 70, "execution_status": "🟢 READY",
        "plan_valid": True, "reasons": ["✅ Trend aligned", "✅ BOS confirmed"],
        "triggers": [], "score_components": [
            {"label": "Trend aligned", "value": 18},
            {"label": "BOS confirmed", "value": 14},
            {"label": "Opposing CHOCH", "value": -6},
        ],
        "market_regime": {"code": "TREND", "risk_multiplier": 1.0},
        "historical_probability": {"samples": 12, "tp1_rate": 66, "reliability": "Moderate"},
    }


def test_conviction_scores_are_ordered_and_explainable():
    result = ConvictionEngine().evaluate(base_analysis())
    assert result["bull_score"] > result["bear_score"]
    assert result["strongest_support"]["label"] == "Trend aligned"
    assert result["action"] == "ENTER"


def test_hard_block_forces_skip():
    data = base_analysis()
    data["reasons"].append("⛔ No aligned structural trigger")
    data = DecisionQualityEngine().enrich(data)
    assert data["conviction"]["action"] == "SKIP"
    assert data["system_decision"] == "SKIP"


def test_live_decision_protects_break_even_trade():
    result = ConvictionEngine().evaluate_live({
        "status": "TP1", "entry": 100, "effective_stop": 100,
        "dynamic_confidence": 75, "realized_r": 1.0,
    })
    assert result["action"] == "PROTECT"
    assert result["protected"] is True
