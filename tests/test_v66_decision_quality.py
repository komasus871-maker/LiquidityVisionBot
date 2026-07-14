from services.decision_quality import DecisionQualityEngine


def base_analysis():
    return {
        "direction": "SHORT",
        "direction_score": 45,
        "setup_score": 52,
        "score": 52,
        "execution_status": "🟢 READY",
        "recommendation": "SELL",
        "plan_valid": True,
        "market_regime": {"code": "TRANSITION"},
        "trend": "🔴 Bearish",
        "structure": "🟡 Range",
        "premium": {"zone": "🔴 Premium"},
        "order_block": "⚪ No Active Order Block",
        "breaker": "🟢 Bullish Breaker",
        "mitigation": "🟢 Bullish Mitigation",
        "fvg": "🟢 Bullish FVG",
        "liquidity": "🟢 Equal Lows",
        "reasons": ["⚠️ Displacement conflicts with direction", "⚠️ Displacement conflicts with direction"],
        "triggers": ["Wait for BOS", "Wait for BOS"],
        "score_components": [
            {"label": "Displacement conflicts with direction", "value": -12, "group": "Entry"},
            {"label": "Displacement conflicts with direction", "value": -8, "group": "Momentum"},
            {"label": "Trend aligned", "value": 18, "group": "Trend"},
        ],
    }


def test_weak_ready_is_downgraded_to_watchlist():
    result = DecisionQualityEngine().enrich(base_analysis())
    assert result["decision_gate_passed"] is False
    assert result["execution_status"] == "🔵 WATCHLIST"
    assert result["plan_mode"] == "AREA_OF_INTEREST"


def test_duplicate_factors_are_counted_once():
    result = DecisionQualityEngine().enrich(base_analysis())
    labels = [x["label"] for x in result["score_components"]]
    assert labels.count("Displacement conflicts with direction") == 1
    assert len(result["reasons"]) == 1
    assert len(result["triggers"]) == 1


def test_expected_path_and_entry_reason_are_exposed():
    data = base_analysis()
    data.update({"direction_score": 75, "setup_score": 68, "score": 68, "execution_status": "🎯 WAIT FOR PULLBACK"})
    result = DecisionQualityEngine().enrich(data)
    assert result["decision_gate_passed"] is True
    assert result["expected_path"][-3:] == ["TP1", "TP2", "TP3"]
    assert result["entry_reasons"]
    assert result["why_trade_exists"]
