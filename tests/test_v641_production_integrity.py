import pytest

from services.trade_plan_integrity import TradePlanIntegrity, InvalidTradePlan


def base(side="SHORT", status="🎯 WAIT FOR PULLBACK"):
    return {
        "direction": side,
        "price": 200.0,
        "entry": 200.0,
        "preferred_entry_low": 206.0 if side == "SHORT" else 190.0,
        "preferred_entry_high": 211.0 if side == "SHORT" else 195.0,
        "stop": 206.4 if side == "SHORT" else 193.0,
        "atr": {"atr": 4.0},
        "execution_status": status,
    }


def test_short_pullback_plan_uses_planned_entry_and_valid_geometry():
    data = base("SHORT")
    TradePlanIntegrity.apply(data)
    assert data["entry"] == pytest.approx(208.5)
    assert data["current_price"] == 200.0
    assert data["tp3"] < data["tp2"] < data["tp1"] < data["entry"] < data["stop"]
    assert data["entry_type"] == "PLANNED_ZONE"


def test_long_pullback_plan_has_valid_geometry():
    data = base("LONG")
    TradePlanIntegrity.apply(data)
    assert data["stop"] < data["entry"] < data["tp1"] < data["tp2"] < data["tp3"]


def test_ready_plan_uses_current_market_price():
    data = base("SHORT", "🟢 READY")
    TradePlanIntegrity.apply(data)
    assert data["entry"] == data["price"]
    assert data["entry_type"] == "MARKET_READY"


def test_validator_rejects_wrong_side_stop():
    with pytest.raises(InvalidTradePlan):
        TradePlanIntegrity.validate("SHORT", 200, 198, 190, 180, 170)
