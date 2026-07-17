from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Argument:
    label: str
    score: float
    source: str = "technical"

    def as_dict(self) -> dict[str, Any]:
        return {"label": self.label, "score": round(self.score, 1), "source": self.source}


class ConvictionEngine:
    """Turns fragmented analytics into one explainable system verdict.

    The engine is deliberately deterministic. It does not replace the existing
    directional model; it reconciles technical evidence, execution quality,
    regime, broad-market context and historical evidence into a stable read
    model that every user-facing surface can consume.
    """

    ACTIONS = {"ENTER", "WAIT", "WATCH", "SKIP", "REDUCE", "EXIT", "HOLD", "PROTECT"}

    @staticmethod
    def _num(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _clean_label(value: Any) -> str:
        text = str(value or "Factor").strip()
        for prefix in ("✅ ", "⚠️ ", "⛔ ", "Strong ", "Moderate "):
            text = text.replace(prefix, "")
        return " ".join(text.split())

    def _technical_arguments(self, data: dict[str, Any]) -> tuple[list[Argument], list[Argument]]:
        direction = str(data.get("direction") or "NEUTRAL").upper()
        bulls: list[Argument] = []
        bears: list[Argument] = []
        for component in data.get("score_components") or []:
            value = self._num(component.get("value"))
            if not value:
                continue
            label = self._clean_label(component.get("label"))
            # Components are scored relative to the selected direction. Convert
            # them into absolute bull/bear arguments for a transparent debate.
            supports_selected = value > 0
            selected_is_bull = direction == "LONG"
            bull_side = supports_selected == selected_is_bull
            argument = Argument(label=label, score=min(25.0, abs(value)), source="technical")
            (bulls if bull_side else bears).append(argument)

        # Preserve direction score as a prior without allowing it to dominate.
        direction_score = self._num(data.get("direction_score", data.get("confidence")), 50.0)
        prior = min(12.0, abs(direction_score - 50.0) * 0.35)
        if prior >= 1.0:
            target = bulls if direction == "LONG" else bears if direction == "SHORT" else None
            if target is not None:
                target.append(Argument("Directional model prior", prior, "model"))
        return bulls, bears

    def _context_arguments(self, data: dict[str, Any], bulls: list[Argument], bears: list[Argument]) -> None:
        direction = str(data.get("direction") or "NEUTRAL").upper()
        context = data.get("global_context") or {}
        adjustment = self._num(context.get("score_adjustment", context.get("adjustment")))
        if adjustment:
            target = bulls if (adjustment > 0) == (direction == "LONG") else bears
            target.append(Argument("Broad-market context", min(12.0, abs(adjustment)), "context"))

        regime = data.get("market_regime") or {}
        code = str(regime.get("code") or "UNKNOWN")
        multiplier = self._num(regime.get("risk_multiplier"), 1.0)
        if code in {"RANGING", "COMPRESSION", "TRANSITION", "TRANSITIONAL"} or multiplier < 0.8:
            # Regime uncertainty opposes execution, not direction. Add it to the
            # opposing camp to reduce directional conviction transparently.
            target = bears if direction == "LONG" else bulls
            target.append(Argument("Regime uncertainty", min(12.0, (1.0 - multiplier) * 25 + 4), "regime"))

    def _historical_modifier(self, data: dict[str, Any]) -> tuple[float, str]:
        exact = data.get("historical_probability") or {}
        similar = data.get("similar_stats") or {}
        source = exact if self._num(exact.get("samples")) >= 5 else similar
        samples = int(self._num(source.get("samples")))
        reliability = str(source.get("reliability") or "Insufficient")
        if samples < 5:
            return 0.0, reliability
        win = self._num(source.get("win_rate", source.get("tp1_rate")), 50.0)
        modifier = max(-8.0, min(8.0, (win - 50.0) * min(1.0, samples / 30.0) * 0.22))
        return modifier, reliability

    @staticmethod
    def _dedupe(arguments: list[Argument]) -> list[Argument]:
        best: dict[str, Argument] = {}
        for arg in arguments:
            key = arg.label.lower()
            if key not in best or arg.score > best[key].score:
                best[key] = arg
        return sorted(best.values(), key=lambda x: x.score, reverse=True)

    def evaluate(self, data: dict[str, Any]) -> dict[str, Any]:
        bulls, bears = self._technical_arguments(data)
        self._context_arguments(data, bulls, bears)
        historical_modifier, historical_reliability = self._historical_modifier(data)
        direction = str(data.get("direction") or "NEUTRAL").upper()
        if historical_modifier:
            target = (bulls if direction == "LONG" else bears) if historical_modifier > 0 else (bears if direction == "LONG" else bulls)
            target.append(Argument("Historical expectancy", abs(historical_modifier), "history"))

        bulls, bears = self._dedupe(bulls), self._dedupe(bears)
        bull_score = min(100.0, sum(a.score for a in bulls))
        bear_score = min(100.0, sum(a.score for a in bears))
        total = max(1.0, bull_score + bear_score)
        margin = abs(bull_score - bear_score)
        directional_confidence = min(95.0, 50.0 + margin / total * 45.0)

        setup = self._num(data.get("setup_score", data.get("score")))
        readiness = self._num(data.get("execution_readiness", data.get("readiness")))
        entry = self._num(data.get("entry_quality"))
        risk = self._num(data.get("risk_quality"), 50.0)
        gate_value = data.get("decision_gate_passed")
        gate = bool(gate_value) if gate_value is not None else (setup >= 62 and readiness >= 62 and entry >= 55)
        status = str(data.get("execution_status") or "")
        plan_valid = bool(data.get("plan_valid", True))
        hard_block = any(str(x).startswith("⛔") for x in data.get("reasons") or [])

        execution_confidence = max(0.0, min(100.0, readiness * .34 + entry * .30 + risk * .16 + setup * .20))
        winner = "BULLS" if bull_score > bear_score else "BEARS" if bear_score > bull_score else "DRAW"
        selected_wins = (direction == "LONG" and winner == "BULLS") or (direction == "SHORT" and winner == "BEARS")

        if not plan_valid or hard_block:
            action = "SKIP"
        elif gate and status == "🟢 READY" and selected_wins and execution_confidence >= 68 and directional_confidence >= 60:
            action = "ENTER"
        elif gate and selected_wins:
            action = "WAIT"
        elif setup >= 52 and selected_wins:
            action = "WATCH"
        else:
            action = "SKIP"

        if execution_confidence >= 75 and directional_confidence >= 70:
            confidence_band = "HIGH"
        elif execution_confidence >= 58 and directional_confidence >= 58:
            confidence_band = "MODERATE"
        else:
            confidence_band = "LOW"

        selected = bulls if direction == "LONG" else bears
        opposing = bears if direction == "LONG" else bulls
        return {
            "action": action,
            "winner": winner,
            "bull_score": round(bull_score, 1),
            "bear_score": round(bear_score, 1),
            "directional_confidence": round(directional_confidence, 1),
            "execution_confidence": round(execution_confidence, 1),
            "confidence_band": confidence_band,
            "supports_direction": selected_wins,
            "bull_arguments": [x.as_dict() for x in bulls[:5]],
            "bear_arguments": [x.as_dict() for x in bears[:5]],
            "strongest_support": selected[0].as_dict() if selected else None,
            "strongest_opposition": opposing[0].as_dict() if opposing else None,
            "historical_reliability": historical_reliability,
            "version": "1.0",
        }

    def evaluate_live(self, signal: dict[str, Any]) -> dict[str, Any]:
        status = str(signal.get("status") or "ACTIVE")
        health = str(signal.get("trade_health") or "").upper()
        confidence = self._num(signal.get("dynamic_confidence", signal.get("confidence")))
        realized = self._num(signal.get("realized_r"))
        effective_stop = self._num(signal.get("effective_stop"), self._num(signal.get("stop")))
        entry = self._num(signal.get("entry"))
        protected = bool(entry and effective_stop and abs(effective_stop - entry) / max(abs(entry), 1e-9) < 1e-5)

        if status in {"STOP", "INVALIDATED", "EXPIRED"}:
            action = "EXIT"
        elif status in {"TP3"}:
            action = "EXIT"
        elif status in {"TP1", "TP2"} or protected:
            action = "PROTECT"
        elif "AT RISK" in health or confidence < 30:
            action = "REDUCE"
        else:
            action = "HOLD"
        return {
            "action": action,
            "confidence": round(confidence, 1),
            "protected": protected,
            "realized_r": round(realized, 2),
            "version": "1.0",
        }
