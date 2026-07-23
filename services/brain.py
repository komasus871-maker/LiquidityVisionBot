"""Compatibility facade for legacy scanner consumers.

The historical ``Brain`` service disappeared while two runtime modules still
imported it.  This facade routes those callers into the current decision model
without duplicating analysis logic.
"""
from __future__ import annotations

from typing import Any

from services.decision_brain import DecisionBrain


class Brain:
    def __init__(self, decision_brain: DecisionBrain | None = None) -> None:
        self._decision_brain = decision_brain or DecisionBrain()

    def build(self, analysis: dict[str, Any]) -> dict[str, Any]:
        decision = self._decision_brain.evaluate(analysis)
        action = str(decision.get("action") or "SKIP")
        score = float(decision.get("score") or decision.get("direction_score") or 0.0)
        return {
            **analysis,
            "score": round(score, 2),
            "probability": round(max(0.0, min(100.0, score)), 2),
            "signal": action,
            "reasons": list(decision.get("reasoning") or []),
            "decision_brain": decision,
        }
