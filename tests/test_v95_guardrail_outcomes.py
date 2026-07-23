from services.copy_guardrail_outcomes import GuardrailOutcomeAnalytics


def test_guardrail_outcomes_quantify_saved_and_missed_trades():
    report = GuardrailOutcomeAnalytics.build([
        {"rejection_code": "MAX_SLIPPAGE", "shadow_realized_r": -1.0},
        {"rejection_code": "MAX_SLIPPAGE", "shadow_realized_r": 2.0},
        {"rejection_code": "LOW_CONFIDENCE", "shadow_realized_r": -0.5},
    ])
    assert report["resolved"] == 3
    assert report["avoided_losses"] == 2
    assert report["missed_wins"] == 1
    assert report["net_shadow_r"] == 0.5
    assert report["by_code"][0].code == "MAX_SLIPPAGE"
    assert report["by_code"][0].resolved == 2


def test_guardrail_outcomes_are_safe_without_samples():
    report = GuardrailOutcomeAnalytics.build([])
    assert report["resolved"] == 0
    assert report["average_shadow_r"] == 0.0
    assert report["by_code"] == []
