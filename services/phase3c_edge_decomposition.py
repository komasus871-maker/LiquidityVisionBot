"""Read-only Phase 3C decomposition of recorded Analyzer decision evidence.

This adapter classifies only fields emitted at decision time.  It cannot read
outcomes to choose a family and never imports a runtime execution component.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping

from services.decision_quality import DecisionQualityEngine
from services.phase3b_edge_surgery import PARENT_BASELINE_CONFIG, _outcome_from_value
from services.research_replay import PerformanceAttribution, ReplayOutcome


PHASE3C_SCHEMA_VERSION = "phase3c-edge-decomposition-v1"
MIN_FAMILY_SAMPLE = 30


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()[:16]


def component_labels(attribution: Mapping[str, Any]) -> frozenset[str]:
    """Return existing score-component labels, preserving no inferred evidence."""
    return frozenset(str(item.get("label")) for item in attribution.get("score_components") or []
                     if isinstance(item, Mapping) and item.get("label"))


def classify_family(analysis: Mapping[str, Any]) -> str:
    """Classify causal Analyzer composition using decision-time evidence only."""
    labels = component_labels(dict(analysis.get("attribution") or analysis))
    direction = str(analysis.get("direction") or "")
    conflict = {"Trend conflicts", "Structure conflicts", "Momentum conflicts"}
    has_confirmation = bool({"Displacement confirmation", "BOS confirmation", "CHOCH confirmation"} & labels)
    if {"Trend conflicts", "CHOCH confirmation"} <= labels:
        return "COUNTERTREND_REVERSAL_LONG" if direction == "LONG" else "COUNTERTREND_REVERSAL_SHORT"
    if {"Trend aligned", "Structure trigger is present"} <= labels and has_confirmation and not (conflict & labels):
        return "TREND_STRUCTURE_CONTINUATION"
    if conflict & labels:
        return "CONFLICTED_EVIDENCE"
    return "MIXED_UNCLASSIFIED" if labels else "UNKNOWN"


@dataclass(frozen=True)
class FamilyCandidate:
    candidate_id: str
    family: str
    rationale: str
    parent_config_hash: str = PARENT_BASELINE_CONFIG

    @property
    def config_hash(self) -> str:
        return _stable_hash({"schema": PHASE3C_SCHEMA_VERSION, "candidate": self.candidate_id,
                             "family": self.family, "rationale": self.rationale,
                             "parent": self.parent_config_hash})


def annotate_families(decisions: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Attach an exhaustive classification without mutating raw decision records."""
    rows: list[dict[str, Any]] = []
    for value in decisions:
        row = dict(value)
        row["phase3c_family"] = classify_family(row)
        rows.append(row)
    return rows


def family_metrics(decisions: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Reconcile family decision and replay metrics; UNKNOWN is intentionally retained."""
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for row in decisions:
        groups.setdefault(str(row.get("phase3c_family") or "UNKNOWN"), []).append(row)
    result: dict[str, dict[str, Any]] = {}
    for family, rows in sorted(groups.items()):
        items = [_outcome_from_value(row["outcome"]) for row in rows if row.get("outcome")]
        result[family] = {"eligible_decisions": len(rows),
                          "approved": sum(row.get("baseline_decision_outcome") == DecisionQualityEngine.APPROVED for row in rows),
                          "metrics": PerformanceAttribution.metrics(items),
                          "insufficient_sample": PerformanceAttribution.metrics(items)["trade_count"] < MIN_FAMILY_SAMPLE}
    return result


def derive_family_admission_overlay(
    baseline_evaluation: Mapping[str, Any], candidate: FamilyCandidate,
) -> dict[str, Any]:
    """Create a research-only family admission overlay from frozen decisions.

    It preserves the existing DecisionQuality approval as a prerequisite and
    keeps entry, exit, cost, and replay outcome unchanged for retained trades.
    """
    outcomes = {_outcome_from_value(item).signal_id: _outcome_from_value(item)
                for item in baseline_evaluation.get("outcomes", [])}
    rows, retained = [], []
    for source in annotate_families(baseline_evaluation.get("decisions", [])):
        row = dict(source)
        old_id = str(row["decision_id"])
        new_id = old_id.replace("BASELINE:", f"{candidate.candidate_id}:", 1)
        accepted = (row.get("baseline_decision_outcome") == DecisionQualityEngine.APPROVED
                    and row["phase3c_family"] == candidate.family)
        row.update({"decision_id": new_id, "candidate_id": candidate.candidate_id,
                    "candidate_config_hash": candidate.config_hash,
                    "candidate_decision_outcome": DecisionQualityEngine.APPROVED if accepted else "NO_TRADE",
                    "candidate_rejection": None if accepted else "PHASE3C_FAMILY_NOT_ADMITTED"})
        prior = outcomes.get(old_id)
        if accepted and prior is not None:
            replayed = replace(prior, signal_id=new_id, strategy_version=candidate.config_hash,
                               provenance={**prior.provenance, "candidate_id": candidate.candidate_id,
                                           "candidate_config_hash": candidate.config_hash,
                                           "phase3c_family": candidate.family,
                                           "derived_family_overlay": True})
            retained.append(replayed)
            row["outcome"] = replayed.as_dict()
        else:
            row["outcome"] = None
        rows.append(row)
    return {"schema": PHASE3C_SCHEMA_VERSION,
            "candidate": {"id": candidate.candidate_id, "family": candidate.family,
                          "rationale": candidate.rationale, "config_hash": candidate.config_hash,
                          "parent_config_hash": candidate.parent_config_hash},
            "role": baseline_evaluation["role"], "interval": dict(baseline_evaluation["interval"]),
            "dataset": dict(baseline_evaluation["dataset"]), "decisions": rows, "outcomes": retained,
            "metrics": PerformanceAttribution.metrics(retained), "family_metrics": family_metrics(rows),
            "derivation": "FAMILY_ADMISSION_ONLY_FROM_DECISION_TIME_ANALYZER_EVIDENCE"}
