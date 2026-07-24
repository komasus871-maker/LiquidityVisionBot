from __future__ import annotations

import pytest

from services.copy_trading import CopyTradingService
from services.execution_models import PositionSizingMode, RiskProfile
from services.execution_validator import ExecutionValidator
from version import APP_VERSION, RELEASE_NAME


def _signal() -> dict:
    return {
        "id": 992,
        "symbol": "BTCUSDT",
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
        "confidence": 80.0,
    }


def test_release_identity() -> None:
    assert APP_VERSION == "9.9.2"
    assert RELEASE_NAME == "Copy Trading Profile Foundation"


def test_profile_validation_normalizes_future_execution_settings() -> None:
    profile = CopyTradingService._validate_profile({
        "risk_pct": 0.75,
        "sizing_mode": "fixed_usdt",
        "fixed_usdt": 250,
        "leverage": 5,
        "auto_copy": 1,
        "max_positions": 4,
    })
    assert profile["sizing_mode"] == "FIXED_USDT"
    assert profile["fixed_usdt"] == 250.0
    assert profile["leverage"] == 5
    assert profile["auto_copy"] == 1


@pytest.mark.parametrize("field,value", [("leverage", 0), ("leverage", 126), ("max_positions", 0)])
def test_profile_validation_rejects_unsafe_limits(field: str, value: int) -> None:
    data = {
        "risk_pct": 0.5, "sizing_mode": "RISK_PERCENT", "fixed_usdt": 0,
        "leverage": 3, "auto_copy": 0, "max_positions": 3,
    }
    data[field] = value
    with pytest.raises(ValueError):
        CopyTradingService._validate_profile(data)


def test_fixed_usdt_sizing_uses_existing_execution_validator() -> None:
    decision = ExecutionValidator().validate(
        signal=_signal(),
        profile=RiskProfile(
            sizing_mode=PositionSizingMode.FIXED_USDT,
            fixed_usdt=250.0,
            leverage=3,
            max_notional_pct=100.0,
        ),
        balance=10_000,
    )
    assert decision.allowed and decision.size is not None
    assert decision.size.notional == 250.0
    assert decision.size.quantity == 2.5
    assert decision.size.risk_amount == 5.0
