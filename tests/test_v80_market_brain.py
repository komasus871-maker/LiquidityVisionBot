from services.decision_brain import DecisionBrain
from services.expected_value import ExpectedValueEngine
from services.market_memory import MarketMemory


def sample():
    return {
        "direction": "LONG", "direction_score": 72, "execution_readiness": 64,
        "entry_quality": 66, "risk_quality": 70, "rr": 3, "blockers": 0,
        "reasons": ["✅ Trend aligned", "⚠️ Low relative volume"],
        "triggers": ["Prefer confirmed volume above 0.85x"],
        "unified_decision": {"action": "WAIT", "score": 64, "reason": "Wait"},
        "historical_intelligence": {"samples": 4, "reliability_score": 10},
        "market_regime": {"label": "Transitional / Mixed"},
    }


def test_expected_value_contract():
    result = ExpectedValueEngine().evaluate(sample())
    assert -2 <= result["expected_r"] <= 3
    assert 0 <= result["rank_score"] <= 100
    assert result["version"] == "8.0"


def test_decision_brain_builds_causal_chain():
    result = DecisionBrain().evaluate(sample())
    assert result["action"] == "WAIT"
    assert len(result["reasoning"]) == 4
    assert result["next_condition"].startswith("Prefer")


def test_market_memory_exposes_rolling_state():
    series = [
        {"at": "a", "direction": "LONG", "direction_score": 55, "readiness": 40, "decision_score": 45, "volume_ratio": .5},
        {"at": "b", "direction": "LONG", "direction_score": 68, "readiness": 57, "decision_score": 62, "volume_ratio": .9},
    ]
    result = MarketMemory.summarize(series)
    assert result["trend_state"] == "IMPROVING"
    assert result["execution_state"] == "IMPROVING"
    assert result["averages"]["direction"] > 60
