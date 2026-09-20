from __future__ import annotations

import json
from typing import Any


APPROVED_DECISION = {
    "decision_authority": "DecisionQualityEngine",
    "decision_version": "decision-authority-v1",
    "decision_source": "TEST_FIXTURE",
    "decision_outcome": "APPROVED",
    "decision_gate_passed": True,
    "decision_path": ["Analyzer", "TradePlanIntegrity", "ProbabilityEngine", "DecisionQualityEngine"],
    "decision_timestamp": "2026-07-24T00:00:00+00:00",
}

APPROVED_FEATURES_JSON = json.dumps({**APPROVED_DECISION, "direction": "LONG"})


def approved_signal(signal: dict[str, Any]) -> dict[str, Any]:
    signal.update(APPROVED_DECISION)
    return signal
