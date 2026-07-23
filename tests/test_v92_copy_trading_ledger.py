from __future__ import annotations

from services.execution_models import PortfolioState, RiskProfile
from services.execution_validator import ExecutionValidator


def signal(**updates):
    value = {
        "id": 501,
        "symbol": "BTC",
        "timeframe": "1h",
        "side": "LONG",
        "status": "ACTIVE",
        "entry": 100.0,
        "current_price": 100.0,
        "stop": 98.0,
        "tp1": 104.0,
        "tp2": 106.0,
        "tp3": 108.0,
        "preferred_entry_low": 99.0,
        "preferred_entry_high": 101.0,
        "confidence": 70.0,
    }
    value.update(updates)
    return value


def validate(sig=None, profile=None, state=None):
    return ExecutionValidator().validate(
        signal=sig or signal(),
        profile=profile or RiskProfile(),
        balance=10_000,
        portfolio=state or PortfolioState(),
    )


def test_daily_loss_limit_is_enforced():
    decision = validate(state=PortfolioState(daily_realized_pnl=-201.0))
    assert not decision.allowed
    assert decision.code == "DAILY_LOSS_LIMIT"


def test_duplicate_symbol_and_cooldown_are_enforced():
    duplicate = validate(state=PortfolioState(symbol_is_open=True))
    cooldown = validate(state=PortfolioState(symbol_in_cooldown=True))
    assert duplicate.code == "SYMBOL_ALREADY_OPEN"
    assert cooldown.code == "SYMBOL_COOLDOWN"


def test_confidence_and_slippage_are_fail_closed():
    low_confidence = validate(sig=signal(confidence=40))
    slippage = validate(sig=signal(current_price=101.0), profile=RiskProfile(max_slippage_pct=0.25))
    assert low_confidence.code == "LOW_CONFIDENCE"
    assert slippage.code == "MAX_SLIPPAGE"


def test_notional_cap_scales_position_without_changing_stop_risk_math():
    decision = validate(profile=RiskProfile(max_notional_pct=10.0))
    assert decision.allowed and decision.size is not None
    assert decision.size.notional == 1000.0
    assert decision.size.quantity == 10.0
    assert decision.size.risk_amount == 20.0
