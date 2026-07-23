from __future__ import annotations

from services.copy_training import CopyTrainingPolicy
from services.execution_models import PortfolioState, RiskProfile
from services.execution_validator import ExecutionValidator


def signal(**updates):
    value = {
        "id": 930,
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
        "confidence": 60.0,
    }
    value.update(updates)
    return value


def validate(policy: CopyTrainingPolicy, confidence: float = 60.0):
    return ExecutionValidator().validate(
        signal=signal(confidence=confidence),
        profile=RiskProfile(min_confidence=55.0, max_notional_pct=100.0),
        balance=10_000.0,
        portfolio=PortfolioState(),
        training_policy=policy,
    )


def test_negative_trained_cohort_fails_closed():
    policy = CopyTrainingPolicy(
        sample_size=20,
        expectancy_r=-0.8,
        confidence_adjustment=-9.6,
        risk_multiplier=0.72,
        blocked=True,
        code="NEGATIVE_COHORT_EDGE",
        reason="negative cohort",
    )
    decision = validate(policy)
    assert not decision.allowed
    assert decision.code == "NEGATIVE_COHORT_EDGE"
    assert decision.training_sample_size == 20


def test_positive_policy_scales_risk_and_preserves_geometry():
    policy = CopyTrainingPolicy(
        sample_size=12,
        expectancy_r=0.5,
        confidence_adjustment=6.0,
        risk_multiplier=1.175,
        code="ADAPTIVE_POLICY",
        reason="positive cohort",
    )
    decision = validate(policy)
    assert decision.allowed and decision.size is not None
    assert decision.risk_multiplier == 1.175
    assert decision.training_sample_size == 12
    assert round(decision.size.risk_amount, 6) == 58.75
    assert round(decision.size.notional, 6) == 2937.5


def test_negative_expectancy_raises_confidence_threshold():
    policy = CopyTrainingPolicy(
        sample_size=10,
        expectancy_r=-0.5,
        confidence_adjustment=-6.0,
        risk_multiplier=0.825,
        code="ADAPTIVE_POLICY",
        reason="weak cohort",
    )
    decision = validate(policy, confidence=60.0)
    assert not decision.allowed
    assert decision.code == "LOW_CONFIDENCE"
    assert "adaptive threshold 61.0" in decision.reason


def test_insufficient_data_keeps_static_execution_policy():
    decision = validate(CopyTrainingPolicy(sample_size=4))
    assert decision.allowed and decision.size is not None
    assert decision.risk_multiplier == 1.0
    assert decision.training_sample_size == 4
