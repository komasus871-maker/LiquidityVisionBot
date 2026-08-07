from __future__ import annotations

from datetime import datetime, timezone

import pytest


def _context():
    from services.ai_trading import AIContext

    return AIContext(
        telegram_id=7,
        signal_id=9918,
        symbol="BTCUSDT",
        timeframe="1h",
        market_timestamp=datetime.now(timezone.utc).isoformat(),
        market={"price": 100},
        features={},
        portfolio={},
        history={},
        deterministic={"direction": "LONG", "status": "ACTIVE"},
        market_checksum="market",
        feature_checksum="features",
    )


def _support(evidence_id: str, statement: str, strength: int):
    return {"evidence_id": evidence_id, "statement": statement, "strength": strength}


def _conflict(evidence_id: str, statement: str, severity: str = "MEDIUM"):
    return {"evidence_id": evidence_id, "statement": statement, "severity": severity}


def _rank(evidence_id: str, rank: int):
    return {"evidence_id": evidence_id, "rank": rank}


def _payload(**overrides):
    payload = {
        "regime": "TREND",
        "direction": "NEUTRAL",
        "confidence": 40,
        "uncertainty": 60,
        "recommended_action": "ABSTAIN",
        "recommended_risk_multiplier": 0,
        "abstention": True,
        "supporting_factors": [
            _support("weaker_structure", "Weaker structure", 70),
            _support("stronger_trend", "Stronger trend", 90),
        ],
        "conflicting_factors": [
            _conflict("elevated_volatility", "Elevated volatility", "HIGH"),
        ],
        "invalidation_conditions": ["Structure breaks"],
        "explanation": "Synthetic advisory observation.",
        "market_regimes": ["TREND"],
        "opportunity_quality": 50,
        # Deliberately unordered as an array: explicit ranks carry the meaning.
        "evidence_ranking": [
            _rank("weaker_structure", 2),
            _rank("stronger_trend", 1),
        ],
        "uncertainty_explanation": "Volatility conflicts with the setup.",
        "symbol": None,
        "reference_price": None,
    }
    payload.update(overrides)
    return payload


def _validate(payload):
    from services.ai_trading import AIResponseValidator

    return AIResponseValidator().validate(payload, _context())


def test_valid_evidence_ranking_is_normalized_by_explicit_rank():
    decision = _validate(_payload())

    assert decision.valid and decision.code == "VALID"
    assert decision.supporting == ("Stronger trend", "Weaker structure")
    assert decision.evidence_ranking == ("Stronger trend", "Weaker structure")
    assert decision.conflicting == ("Elevated volatility",)


def test_duplicate_ranks_fail_closed():
    decision = _validate(_payload(evidence_ranking=[
        _rank("stronger_trend", 1),
        _rank("weaker_structure", 1),
    ]))

    assert not decision.valid and decision.code == "EVIDENCE_RANK_DUPLICATE"
    assert decision.validation_stage == "SEMANTIC_VALIDATION"


def test_missing_ranked_evidence_reference_fails_closed():
    decision = _validate(_payload(evidence_ranking=[
        _rank("stronger_trend", 1),
        _rank("evidence_not_present", 2),
    ]))

    assert not decision.valid and decision.code == "EVIDENCE_RANK_REFERENCE_MISSING"


def test_out_of_range_rank_fails_closed():
    decision = _validate(_payload(evidence_ranking=[
        _rank("stronger_trend", 1),
        _rank("weaker_structure", 3),
    ]))

    assert not decision.valid and decision.code == "EVIDENCE_RANK_OUT_OF_RANGE"


def test_production_semantic_violation_cannot_rank_contradictory_evidence():
    """Reproduces the production failure class without a provider request."""
    decision = _validate(_payload(evidence_ranking=[
        _rank("stronger_trend", 1),
        _rank("elevated_volatility", 2),
    ]))

    assert not decision.valid
    assert decision.code == "EVIDENCE_RANK_CONFLICTING_REFERENCE"
    assert decision.validation_stage == "SEMANTIC_VALIDATION"


def test_equal_strength_ties_use_evidence_id_as_secondary_rule():
    supports = [
        _support("zeta_signal", "Zeta signal", 80),
        _support("alpha_signal", "Alpha signal", 80),
    ]
    valid = _validate(_payload(
        supporting_factors=supports,
        evidence_ranking=[_rank("zeta_signal", 2), _rank("alpha_signal", 1)],
    ))
    invalid = _validate(_payload(
        supporting_factors=list(reversed(supports)),
        evidence_ranking=[_rank("zeta_signal", 1), _rank("alpha_signal", 2)],
    ))

    assert valid.valid and valid.evidence_ranking == ("Alpha signal", "Zeta signal")
    assert not invalid.valid and invalid.code == "EVIDENCE_RANK_ORDER_INVALID"


def test_unordered_evidence_arrays_do_not_change_semantic_normalization():
    first = _validate(_payload())
    second = _validate(_payload(
        supporting_factors=list(reversed(_payload()["supporting_factors"])),
        conflicting_factors=list(reversed(_payload()["conflicting_factors"])),
        evidence_ranking=list(reversed(_payload()["evidence_ranking"])),
    ))

    assert first.valid and second.valid
    assert first.supporting == second.supporting
    assert first.conflicting == second.conflicting
    assert first.evidence_ranking == second.evidence_ranking


def test_omitted_ranking_is_not_silently_normalized_when_support_exists():
    decision = _validate(_payload(evidence_ranking=[]))

    assert not decision.valid and decision.code == "EVIDENCE_RANKING_INCOMPLETE"


def test_empty_ranking_is_explicitly_valid_only_without_supporting_evidence():
    decision = _validate(_payload(supporting_factors=[], evidence_ranking=[]))

    assert decision.valid and decision.evidence_ranking == () and decision.supporting == ()


def test_duplicate_ranked_reference_fails_closed():
    decision = _validate(_payload(evidence_ranking=[
        _rank("stronger_trend", 1),
        _rank("stronger_trend", 2),
    ]))

    assert not decision.valid and decision.code == "EVIDENCE_RANK_REFERENCE_DUPLICATE"


def test_same_statement_cannot_be_supporting_and_conflicting():
    decision = _validate(_payload(conflicting_factors=[
        _conflict("same_fact_conflict", "Stronger trend", "CRITICAL"),
    ]))

    assert not decision.valid and decision.code == "EVIDENCE_CLASSIFICATION_CONFLICT"


@pytest.mark.parametrize("rank", [0, 21])
def test_rank_outside_schema_bounds_is_rejected_structurally(rank):
    decision = _validate(_payload(evidence_ranking=[
        _rank("stronger_trend", 1),
        _rank("weaker_structure", rank),
    ]))

    assert not decision.valid and decision.code == "SCHEMA_VALIDATION_FAILED"
    assert decision.validation_stage == "JSON_SCHEMA_VALIDATION"
