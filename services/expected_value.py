from __future__ import annotations

from typing import Any


class ExpectedValueEngine:
    """Conservative EV proxy used for ranking before probability is calibrated.

    Historical expectancy is trusted only in proportion to reliability. The
    remaining estimate comes from execution quality and the planned RR, with
    explicit penalties for blockers and non-executable actions.
    """

    @staticmethod
    def _clamp(value: float, low: float, high: float) -> float:
        return max(low, min(high, float(value)))

    def evaluate(self, analysis: dict[str, Any]) -> dict[str, Any]:
        decision = analysis.get("unified_decision") or {}
        score = float(decision.get("score") or 0)
        action = str(decision.get("action") or "SKIP")
        rr = max(0.0, float(analysis.get("rr") or 0))
        execution = float(analysis.get("execution_readiness") or 0)
        entry = float(analysis.get("entry_quality") or 0)
        risk = float(analysis.get("risk_quality") or 50)
        blockers = int(analysis.get("blockers") or 0)

        # Model win probability is intentionally shrunk toward 50% until the
        # historical engine has a reliable sample.
        model_probability = 0.50 + (score - 50.0) / 250.0
        model_probability += (execution - 50.0) / 700.0
        model_probability += (entry - 50.0) / 900.0
        model_probability += (risk - 50.0) / 1200.0
        model_probability = self._clamp(model_probability, 0.22, 0.78)

        model_ev = model_probability * rr - (1.0 - model_probability)
        historical = analysis.get("historical_intelligence") or {}
        hist_ev = historical.get("expected_r")
        reliability = self._clamp(float(historical.get("reliability_score") or 0) / 100.0, 0.0, 1.0)
        effective = float(historical.get("effective_samples") or historical.get("samples") or 0)
        history_weight = min(0.65, reliability * min(1.0, effective / 30.0))
        blended_ev = model_ev * (1.0 - history_weight)
        if hist_ev is not None:
            blended_ev += float(hist_ev) * history_weight

        action_penalty = {"TAKE": 0.0, "WAIT": 0.10, "SKIP": 0.30, "INVALID": 1.0}.get(action, 0.25)
        blended_ev -= action_penalty + min(0.45, blockers * 0.08)
        expected_r = round(self._clamp(blended_ev, -2.0, 3.0), 2)
        rank_score = round(self._clamp(50.0 + expected_r * 22.0 + score * 0.35, 0.0, 100.0), 1)

        band = "POSITIVE" if expected_r >= 0.25 else "MARGINAL" if expected_r >= 0 else "NEGATIVE"
        confidence = "CALIBRATED" if history_weight >= 0.45 else "DEVELOPING" if history_weight >= 0.15 else "MODEL-BASED"
        return {
            "expected_r": expected_r,
            "rank_score": rank_score,
            "win_probability": round(model_probability * 100.0, 1),
            "band": band,
            "confidence": confidence,
            "history_weight": round(history_weight, 3),
            "version": "8.0",
        }
