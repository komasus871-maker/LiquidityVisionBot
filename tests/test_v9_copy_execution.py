from services.execution_models import RiskProfile
from services.execution_validator import ExecutionValidator
from services.position_sizer import PositionSizer


def _signal(**updates):
    signal = {
        "id": 1, "symbol": "BTC", "timeframe": "1h", "side": "LONG", "status": "ACTIVE",
        "entry": 100.0, "current_price": 100.0, "stop": 98.0, "tp1": 104.0, "tp2": 106.0,
        "tp3": 108.0, "preferred_entry_low": 99.0, "preferred_entry_high": 101.0,
        "activated_at": "2026-07-23T00:00:00+00:00",
    }
    signal.update(updates)
    return signal


def test_position_sizer_uses_stop_distance():
    size = PositionSizer.calculate(balance=10_000, risk_pct=0.5, entry=100, stop=98)
    assert size.risk_amount == 50
    assert size.quantity == 25
    assert size.notional == 2500


def test_execution_validator_approves_valid_plan():
    decision = ExecutionValidator().validate(
        signal=_signal(), profile=RiskProfile(), balance=10_000, open_positions=0, current_heat_r=0,
    )
    assert decision.allowed
    assert decision.size is not None


def test_execution_validator_blocks_heat_and_bad_geometry():
    validator = ExecutionValidator()
    heat = validator.validate(signal=_signal(), profile=RiskProfile(max_heat_r=2.5), balance=10_000, open_positions=1, current_heat_r=2.0)
    assert not heat.allowed and heat.code == "MAX_HEAT"
    geometry = validator.validate(signal=_signal(stop=102), profile=RiskProfile(), balance=10_000, open_positions=0, current_heat_r=0)
    assert not geometry.allowed and geometry.code == "INVALID_GEOMETRY"


def test_execution_validator_rejects_non_active_signal():
    decision = ExecutionValidator().validate(signal=_signal(status="WATCHING"), profile=RiskProfile(), balance=10_000, open_positions=0, current_heat_r=0)
    assert not decision.allowed and decision.code == "SIGNAL_NOT_ACTIVE"
