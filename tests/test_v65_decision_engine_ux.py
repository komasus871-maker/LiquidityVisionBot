from pathlib import Path
from services.report import Report


def sample():
    return {
        "symbol": "BTC", "timeframe": "4h", "market_bias": "🔴 Strong Bearish",
        "direction": "SHORT", "direction_score": 83, "score": 62, "ai_grade": "B",
        "execution_status": "🟡 WAIT FOR TRIGGER", "recommendation": "🟡 CONDITIONAL SELL",
        "current_price": 62264.1, "price": 62264.1, "entry": 62032.4,
        "entry_type": "PLANNED_ZONE", "preferred_entry_low": 61800.7,
        "preferred_entry_high": 62264.1, "stop": 63199.44,
        "tp1": 60865.37, "tp2": 59698.33, "tp3": 58531.3, "rr": 3,
        "reasons": ["✅ Trend aligned", "✅ BOS confirmation", "⚠️ Opposing FVG"],
        "triggers": ["Wait for the market regime to confirm a trend"],
        "alternative_conditions": ["Long BOS/CHOCH"],
        "market_regime": {"label": "⚪ Transitional / Mixed", "risk_multiplier": 0.65,
                          "execution_mode": "REQUIRE EXTRA CONFIRMATION", "confidence": 58.4,
                          "trend_strength": 24.4, "efficiency": 15.4,
                          "volatility_state": "NORMAL", "volatility_percentile": 25.6},
        "direction_breakdown": {"Trend": 18, "Structure": 22, "Liquidity/SMC": -14, "Momentum": 17},
        "premium": {"zone": "🔴 Premium", "premium": 64.68, "low": 57750,
                    "equilibrium": 61239.5, "high": 64729},
        "trend": "🔴 Bearish", "structure": "🟡 Range", "bos": "🔴 Bearish BOS",
        "choch": "🔴 Bearish CHOCH", "liquidity": "Equal Highs", "sweep": "Internal",
        "order_block": "None", "breaker": "Bullish Breaker", "mitigation": "Bullish Mitigation",
        "fvg": "Bullish FVG", "ema50": 63158, "ema200": 63769, "rsi": 35.2,
        "macd": "Bearish", "volume": "Elevated", "displacement": "Moderate Bearish",
        "atr": {"atr": 623.5}, "entry_quality": 98, "risk_quality": 82,
    }


def test_decision_card_is_action_first_and_compact():
    text = Report().build(sample())
    assert "🟡 WAIT" in text
    assert "Trade Quality" in text
    assert "Planned Entry" in text
    assert "Direction score" not in text
    assert len(text) < 2500


def test_why_not_explains_delayed_execution():
    text = Report().why_not(sample())
    assert "Why NOT?" in text
    assert "activation trigger is still missing" in text
    assert "Waiting protects expectancy" in text


def test_scenario_map_uses_visual_flow():
    text = Report().scenarios(sample())
    assert "↓" in text
    assert "Invalidation" in text
    assert "TP3" in text



def test_keyboard_source_contains_why_not():
    source = Path("keyboards/analysis_actions.py").read_text()
    assert "whynot:{symbol}:{timeframe}" in source
