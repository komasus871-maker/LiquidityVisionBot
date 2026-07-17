from __future__ import annotations

from typing import Any

from services.expected_value import ExpectedValueEngine


class DecisionBrain:
    """Transforms model outputs into one causal, user-facing decision report."""

    def __init__(self) -> None:
        self.ev = ExpectedValueEngine()

    @staticmethod
    def _clean(value: Any) -> str:
        text = str(value or "").strip()
        for prefix in ("✅ ", "⚠️ ", "⛔ ", "🔔 "):
            text = text.replace(prefix, "")
        return text

    def evaluate(self, analysis: dict[str, Any]) -> dict[str, Any]:
        unified = analysis.get("unified_decision") or {}
        action = str(unified.get("action") or "SKIP")
        direction = str(analysis.get("direction") or "NEUTRAL")
        direction_score = float(analysis.get("direction_score") or 0)
        execution = float(analysis.get("execution_readiness") or 0)
        entry = float(analysis.get("entry_quality") or 0)
        regime = analysis.get("market_regime") or {}
        regime_label = str(regime.get("label") or "Unknown") if isinstance(regime, dict) else str(regime)

        strengths = [self._clean(x) for x in analysis.get("reasons") or [] if str(x).startswith("✅")]
        blockers = [self._clean(x) for x in analysis.get("reasons") or [] if str(x).startswith(("⚠️", "⛔"))]
        triggers = [self._clean(x) for x in analysis.get("triggers") or []]

        thesis = (
            f"Directional evidence favors {direction} ({direction_score:.0f}/100)."
            if direction in {"LONG", "SHORT"}
            else "Directional evidence is balanced and no side owns a clear edge."
        )
        confirmation = (
            f"The thesis is supported by {strengths[0].lower()}." if strengths
            else "The thesis has no dominant technical confirmation yet."
        )
        conflict = (
            f"Execution is constrained by {blockers[0].lower()}." if blockers
            else f"Execution quality is {execution:.0f}/100 in a {regime_label.lower()} regime."
        )
        consequence = {
            "TAKE": "The combined evidence supports execution under the current risk plan.",
            "WAIT": "The thesis remains valid, but activation must wait for confirmation.",
            "SKIP": "The expected value is too weak to allocate risk now.",
            "INVALID": "The plan is invalid and must not be executed.",
        }.get(action, "The system remains in observation mode.")

        ev = self.ev.evaluate(analysis)
        next_condition = triggers[0] if triggers else "A material improvement in execution quality"
        reasoning = [thesis, confirmation, conflict, consequence]
        return {
            "action": action,
            "score": float(unified.get("score") or 0),
            "direction_score": round(direction_score, 1),
            "execution_score": round(execution, 1),
            "entry_score": round(entry, 1),
            "reasoning": reasoning,
            "primary_reason": blockers[0] if blockers else (strengths[0] if strengths else unified.get("reason")),
            "next_condition": next_condition,
            "expected_value": ev,
            "version": "8.0",
        }
