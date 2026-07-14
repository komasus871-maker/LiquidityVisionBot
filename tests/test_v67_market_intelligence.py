from services.market_context import MarketContextEngine
from services.decision_quality import DecisionQualityEngine


def base(side="LONG", score=70):
    return {
        "direction": side,
        "direction_score": score,
        "setup_score": 70,
        "score": 70,
        "execution_readiness": 70,
        "readiness": 70,
        "reasons": [],
        "triggers": [],
        "execution_status": "🟢 READY",
        "market_regime": {"code": "TRENDING"},
        "plan_valid": True,
        "trend": "🟢 Bullish" if side == "LONG" else "🔴 Bearish",
        "structure": "🟢 Bullish" if side == "LONG" else "🔴 Bearish",
    }


def test_btc_conflict_reduces_setup_and_adds_trigger():
    alt = base("LONG")
    btc = base("SHORT", 80)
    out = MarketContextEngine().enrich(alt, symbol="TAO", btc=btc)
    assert out["global_context"]["alignment"] == "CONFLICT"
    assert out["setup_score"] == 60
    assert any("BTC context" in x for x in out["reasons"])
    assert any("BTC context" in x for x in out["triggers"])


def test_btc_alignment_is_small_positive_not_signal_creation():
    alt = base("SHORT")
    btc = base("SHORT", 82)
    out = MarketContextEngine().enrich(alt, symbol="TAO", btc=btc)
    assert out["global_context"]["alignment"] == "ALIGNED"
    assert out["setup_score"] == 73


def test_quality_gate_maps_action_and_take_decision():
    data = base("LONG", 80)
    data["setup_score"] = data["score"] = 75
    out = DecisionQualityEngine().enrich(data)
    assert out["decision_action"] == "EXECUTE"
    assert out["would_take_trade"] is True


def test_low_quality_becomes_watch_or_skip():
    data = base("LONG", 55)
    data["setup_score"] = data["score"] = 54
    data["execution_status"] = "🟡 WAIT FOR TRIGGER"
    out = DecisionQualityEngine().enrich(data)
    assert out["decision_gate_passed"] is False
    assert out["decision_action"] == "WATCH"
