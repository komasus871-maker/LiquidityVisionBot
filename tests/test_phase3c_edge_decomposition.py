from __future__ import annotations

import inspect

from services.decision_quality import DecisionQualityEngine
from services.phase3c_edge_decomposition import (
    FamilyCandidate, annotate_families, classify_family, derive_family_admission_overlay,
)
from services.research_replay import ReplayOutcome


def _record(direction="LONG", labels=("Trend conflicts", "CHOCH confirmation")):
    return {"decision_id": "BASELINE:BTC:1h:2026-01-01T00:00:00+00:00", "direction": direction,
            "baseline_decision_outcome": DecisionQualityEngine.APPROVED,
            "attribution": {"score_components": [{"label": label, "value": 1} for label in labels]}}


def _outcome(signal_id):
    return ReplayOutcome(signal_id, "baseline", "BTC", "1h", "LONG", "2026-01-01T00:00:00+00:00",
                         "2026-01-01T00:00:00+00:00", "2026-01-01T01:00:00+00:00",
                         "2026-01-01T02:00:00+00:00", 100, 100, None, 98, (102,), 102, "TP",
                         2, 0, 0, 0, None, "NOT_MODELED", 2, 1, 1, 1, 0,
                         provenance={"evaluation_mode": "HISTORICAL_REPLAY"})


def test_family_classifier_is_exhaustive_directional_and_has_no_outcome_input():
    assert classify_family(_record()) == "COUNTERTREND_REVERSAL_LONG"
    assert classify_family(_record("SHORT")) == "COUNTERTREND_REVERSAL_SHORT"
    assert classify_family(_record(labels=())) == "UNKNOWN"
    annotated = annotate_families([_record(), _record("SHORT"), _record(labels=())])
    assert {row["phase3c_family"] for row in annotated} == {
        "COUNTERTREND_REVERSAL_LONG", "COUNTERTREND_REVERSAL_SHORT", "UNKNOWN"}
    assert "outcome" not in inspect.getsource(classify_family)


def test_family_overlay_retains_only_existing_decisionquality_approval_and_is_reproducible():
    record = _record()
    evaluation = {"role": "DEVELOPMENT", "interval": {}, "dataset": {}, "decisions": [record],
                  "outcomes": [_outcome(record["decision_id"])]}
    candidate = FamilyCandidate("F1", "COUNTERTREND_REVERSAL_LONG", "existing evidence")
    same = FamilyCandidate("F1", "COUNTERTREND_REVERSAL_LONG", "existing evidence")
    changed = FamilyCandidate("F1", "COUNTERTREND_REVERSAL_LONG", "different rationale")
    result = derive_family_admission_overlay(evaluation, candidate)
    assert candidate.config_hash == same.config_hash != changed.config_hash
    assert result["metrics"]["trade_count"] == 1
    assert result["decisions"][0]["candidate_decision_outcome"] == DecisionQualityEngine.APPROVED
    assert result["outcomes"][0].signal_id.startswith("F1:")
