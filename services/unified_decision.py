from __future__ import annotations

from typing import Any


class UnifiedDecisionEngine:
    """One deterministic decision contract for Analyze, Scanner and Watch.

    The engine does not replace the underlying analyzers. It reconciles their
    outputs into one score, one action and an auditable contribution ledger.
    """

    ACTION_TAKE = "TAKE"
    ACTION_WAIT = "WAIT"
    ACTION_SKIP = "SKIP"
    ACTION_INVALID = "INVALID"

    @staticmethod
    def _clamp(value: float) -> float:
        return round(max(0.0, min(100.0, float(value))), 1)

    @staticmethod
    def _contribution(label: str, value: float, source: str) -> dict[str, Any]:
        return {"label": label, "value": round(float(value), 1), "source": source}

    def evaluate(self, analysis: dict[str, Any]) -> dict[str, Any]:
        direction = float(analysis.get("direction_score") or analysis.get("score") or 50)
        entry = float(analysis.get("entry_quality") or 50)
        risk = float(analysis.get("risk_quality") or 50)
        readiness = float(analysis.get("execution_readiness") or 50)
        rr = float(analysis.get("rr") or 0)
        reason_items = [str(x) for x in (analysis.get("reasons") or [])]
        hard_reason_blockers = sum(1 for x in reason_items if x.startswith("⛔"))
        blockers = max(int(analysis.get("blockers") or 0), hard_reason_blockers)
        status = str(analysis.get("execution_status") or "")

        base = direction * 0.27 + entry * 0.23 + risk * 0.16 + readiness * 0.34
        contributions = [
            self._contribution("Directional evidence", (direction - 50) * 0.27, "direction"),
            self._contribution("Entry efficiency", (entry - 50) * 0.23, "execution"),
            self._contribution("Risk quality", (risk - 50) * 0.16, "risk"),
            self._contribution("Activation readiness", (readiness - 50) * 0.34, "readiness"),
        ]

        rr_bonus = 0.0
        if rr >= 3:
            rr_bonus = 5.0
        elif rr >= 2:
            rr_bonus = 3.0
        elif rr and rr < 1.35:
            rr_bonus = -10.0
        base += rr_bonus
        if rr_bonus:
            contributions.append(self._contribution("Risk/reward profile", rr_bonus, "rr"))

        regime = analysis.get("regime") or analysis.get("regime_diagnostics") or {}
        regime_text = " ".join(str(v) for v in regime.values()) if isinstance(regime, dict) else str(regime)
        if "AVOID TREND" in regime_text.upper() or "CHOP" in regime_text.upper() or "RANG" in regime_text.upper():
            base -= 9.0
            contributions.append(self._contribution("Hostile ranging regime", -9.0, "regime"))

        historical = analysis.get("historical_intelligence") or {}
        samples = int(historical.get("samples") or 0)
        reliability = float(historical.get("reliability_score") or 0)
        expectancy = historical.get("expected_r")
        if samples >= 5 and expectancy is not None:
            hist_adjustment = max(-10.0, min(10.0, float(expectancy) * 7.0)) * max(0.25, reliability / 100)
            base += hist_adjustment
            contributions.append(self._contribution("Historical expectancy", hist_adjustment, "history"))

        if blockers:
            penalty = min(18.0, blockers * 4.0)
            base -= penalty
            contributions.append(self._contribution("Hard execution blockers", -penalty, "blockers"))

        score = self._clamp(base)
        invalid = "INVALID" in status.upper() or bool(analysis.get("plan_invalid"))
        ready = "READY" in status.upper() and "WAIT" not in status.upper()
        if invalid:
            action, reason = self.ACTION_INVALID, "The current plan is invalid under the unified lifecycle rules."
        elif blockers > 0:
            action, reason = self.ACTION_SKIP, "A hard execution blocker prevents activation under the unified rules."
        elif ready and score >= 70:
            action, reason = self.ACTION_TAKE, "Direction, execution and risk are aligned with no hard blocker."
        elif score >= 48 and direction >= 55:
            action, reason = self.ACTION_WAIT, "The directional thesis remains valid, but execution quality is incomplete."
        else:
            action, reason = self.ACTION_SKIP, "Current expectancy is not strong enough to justify execution."

        conviction = "HIGH" if score >= 78 else "MEDIUM" if score >= 62 else "LOW"
        positive = sorted((x for x in contributions if x["value"] > 0), key=lambda x: x["value"], reverse=True)
        negative = sorted((x for x in contributions if x["value"] < 0), key=lambda x: x["value"])
        return {
            "score": score,
            "action": action,
            "conviction": conviction,
            "reason": reason,
            "contributions": contributions,
            "top_support": positive[:3],
            "top_opposition": negative[:3],
            "version": "8.0",
        }
