from services.unified_decision import UnifiedDecisionEngine
from services.market_memory import MarketMemory


def _analysis():
    return {
        "direction_score": 80,
        "entry_quality": 76,
        "risk_quality": 82,
        "execution_readiness": 78,
        "rr": 3.0,
        "blockers": 0,
        "execution_status": "🟢 READY",
        "historical_intelligence": {"samples": 8, "expected_r": 0.7, "reliability_score": 30},
    }


def test_unified_take_requires_ready_quality():
    result = UnifiedDecisionEngine().evaluate(_analysis())
    assert result["action"] == "TAKE"
    assert result["score"] >= 70
    assert result["top_support"]


def test_unified_never_takes_missing_trigger():
    data = _analysis()
    data.update({"execution_status": "🟡 WAIT FOR TRIGGER", "execution_readiness": 44})
    assert UnifiedDecisionEngine().evaluate(data)["action"] != "TAKE"


def test_memory_detects_deterioration():
    rows = [
        {"at": "a", "direction": "LONG", "decision_score": 76, "direction_score": 80, "readiness": 72, "volume_ratio": 1.1},
        {"at": "b", "direction": "LONG", "decision_score": 55, "direction_score": 67, "readiness": 45, "volume_ratio": 0.5},
    ]
    result = MarketMemory.summarize(rows)
    assert result["state"] == "DETERIORATING"
    assert result["changes"]
