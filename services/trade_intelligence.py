from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class IntelligenceSnapshot:
    confidence: float
    confidence_delta: float
    health: str
    health_score: float
    trend: float
    structure: float
    liquidity: float
    momentum: float
    commentary: str
    alert_reasons: list[str]
    risk_used: float
    distance_to_stop: float
    mfe_giveback: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TradeIntelligenceEngine:
    """Deterministic live-trade intelligence.

    The engine intentionally separates statistical probability from model
    confidence. Confidence is a transparent health/context score; historical
    probabilities are supplied separately by ProbabilityEngine.
    """

    HEALTH_LEVELS = (
        (78, "🟢 HEALTHY"),
        (60, "🟡 STABLE"),
        (42, "🟠 WEAKENING"),
        (0, "🔴 AT RISK"),
    )

    @staticmethod
    def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
        return max(low, min(high, float(value)))

    @staticmethod
    def _load_features(signal: dict) -> dict[str, Any]:
        raw = signal.get("features_json") or "{}"
        if isinstance(raw, dict):
            return raw
        try:
            return json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return {}

    @classmethod
    def _static_components(cls, signal: dict) -> dict[str, float]:
        features = cls._load_features(signal)
        breakdown = features.get("direction_breakdown") or {}
        values: dict[str, float] = {}
        for key, label in (
            ("Trend", "trend"),
            ("Structure", "structure"),
            ("Liquidity/SMC", "liquidity"),
            ("Momentum", "momentum"),
        ):
            raw = float(breakdown.get(key, 0) or 0)
            values[label] = cls._clamp(50 + raw * 2)
        return values

    @classmethod
    def _live_components(cls, signal: dict, df) -> dict[str, float]:
        side = str(signal.get("side") or "LONG")
        sign = 1 if side == "LONG" else -1
        static = cls._static_components(signal)
        if df is None or len(df) < 6:
            return static

        closes = df["close"].astype(float)
        opens = df["open"].astype(float)
        highs = df["high"].astype(float)
        lows = df["low"].astype(float)
        volumes = df["volume"].astype(float) if "volume" in df.columns else None

        ema_fast = closes.ewm(span=min(5, len(closes)), adjust=False).mean().iloc[-1]
        ema_slow = closes.ewm(span=min(13, len(closes)), adjust=False).mean().iloc[-1]
        trend_alignment = sign * (ema_fast - ema_slow) / max(abs(ema_slow), 1e-12) * 10000
        trend = cls._clamp(static["trend"] * 0.55 + cls._clamp(50 + trend_alignment * 5) * 0.45)

        recent = closes.iloc[-5:]
        slope = sign * (float(recent.iloc[-1]) - float(recent.iloc[0])) / max(abs(float(recent.iloc[0])), 1e-12) * 10000
        aligned_candles = sum(
            1 for o, c in zip(opens.iloc[-5:], closes.iloc[-5:])
            if (c > o and side == "LONG") or (c < o and side == "SHORT")
        )
        structure_live = cls._clamp(35 + aligned_candles * 10 + slope * 2)
        structure = cls._clamp(static["structure"] * 0.6 + structure_live * 0.4)

        entry = float(signal.get("entry") or closes.iloc[-1])
        stop = float(signal.get("effective_stop") or signal.get("stop") or entry)
        price = float(closes.iloc[-1])
        total_risk = max(abs(entry - stop), 1e-12)
        favorable = ((price - entry) if side == "LONG" else (entry - price)) / total_risk
        liquidity_live = cls._clamp(55 + favorable * 12)
        liquidity = cls._clamp(static["liquidity"] * 0.7 + liquidity_live * 0.3)

        returns = closes.pct_change().fillna(0)
        directional_return = sign * float(returns.iloc[-3:].sum()) * 10000
        body = abs(float(closes.iloc[-1]) - float(opens.iloc[-1]))
        candle_range = max(float(highs.iloc[-1]) - float(lows.iloc[-1]), 1e-12)
        body_ratio = body / candle_range
        last_aligned = (closes.iloc[-1] > opens.iloc[-1]) if side == "LONG" else (closes.iloc[-1] < opens.iloc[-1])
        volume_score = 50.0
        if volumes is not None and len(volumes) >= 6:
            baseline = float(volumes.iloc[-6:-1].mean())
            ratio = float(volumes.iloc[-1]) / max(baseline, 1e-12)
            volume_score = cls._clamp(35 + ratio * 25)
        momentum_live = cls._clamp(
            42 + directional_return * 4 + (18 if last_aligned else -12) * body_ratio + (volume_score - 50) * 0.35
        )
        momentum = cls._clamp(static["momentum"] * 0.45 + momentum_live * 0.55)
        return {"trend": trend, "structure": structure, "liquidity": liquidity, "momentum": momentum}

    @classmethod
    def _health_label(cls, score: float) -> str:
        for minimum, label in cls.HEALTH_LEVELS:
            if score >= minimum:
                return label
        return "🔴 AT RISK"

    def evaluate(self, signal: dict, price: float, df=None) -> IntelligenceSnapshot:
        side = str(signal.get("side") or "LONG")
        entry = float(signal.get("entry") or price)
        stop = float(signal.get("effective_stop") or signal.get("stop") or entry)
        risk = max(abs(entry - stop), 1e-12)
        remaining = (price - stop) if side == "LONG" else (stop - price)
        distance_to_stop = self._clamp(remaining / risk * 100)
        risk_used = self._clamp(100 - distance_to_stop)
        current_r = ((price - entry) if side == "LONG" else (entry - price)) / risk

        components = self._live_components(signal, df)
        base_confidence = (
            components["trend"] * 0.28
            + components["structure"] * 0.27
            + components["liquidity"] * 0.20
            + components["momentum"] * 0.25
        )
        mfe = max(0.0, float(signal.get("max_profit_pct") or 0))
        current_move = ((price - entry) / max(abs(entry), 1e-12) * 100) * (1 if side == "LONG" else -1)
        mfe_giveback = max(0.0, mfe - current_move)

        raw_confidence = base_confidence
        raw_confidence += max(-16, min(12, current_r * 9))
        raw_confidence -= max(0, risk_used - 60) * 0.28
        raw_confidence -= min(14, mfe_giveback * 3.0)
        if signal.get("tp1_hit_at"):
            raw_confidence += 5
        if signal.get("break_even_at"):
            raw_confidence += 4
        raw_confidence = self._clamp(raw_confidence)

        previous_confidence = float(signal.get("dynamic_confidence") or signal.get("confidence") or raw_confidence)
        # Exponential smoothing prevents 40→78→43 oscillations on every candle.
        # Near-stop states react faster so critical deterioration is never hidden.
        alpha = 0.65 if risk_used >= 85 else 0.35
        confidence = round(self._clamp(previous_confidence * (1 - alpha) + raw_confidence * alpha), 1)
        confidence_delta = round(confidence - previous_confidence, 1)

        raw_health = confidence
        raw_health -= max(0, risk_used - 55) * 0.35
        raw_health += max(-12, min(10, current_r * 7))
        raw_health -= min(18, mfe_giveback * 4)
        raw_health = self._clamp(raw_health)
        previous_health_score = float(signal.get("health_score") or raw_health)
        health_score = round(self._clamp(previous_health_score * 0.65 + raw_health * 0.35), 1)
        previous_health = str(signal.get("trade_health") or "")
        candidate_health = self._health_label(health_score)

        # Four-point hysteresis around category borders keeps health from flipping
        # STABLE↔WEAKENING every minute on tiny price moves.
        health = candidate_health
        if previous_health:
            boundaries = {"🟢 HEALTHY": 78, "🟡 STABLE": 60, "🟠 WEAKENING": 42, "🔴 AT RISK": 0}
            prev_min = boundaries.get(previous_health, 0)
            if previous_health == "🟢 HEALTHY" and health_score >= 74:
                health = previous_health
            elif previous_health == "🟡 STABLE" and 56 <= health_score < 82:
                health = previous_health
            elif previous_health == "🟠 WEAKENING" and 38 <= health_score < 64:
                health = previous_health
            elif previous_health == "🔴 AT RISK" and health_score < 46:
                health = previous_health

        reasons: list[str] = []
        if previous_health and previous_health != health:
            reasons.append(f"Trade health changed: {previous_health} → {health}")
        threshold = 10.0
        if abs(confidence_delta) >= threshold:
            reasons.append(f"Confidence changed: {previous_confidence:.0f}% → {confidence:.0f}%")
        previous_risk = float(signal.get("last_risk_used") or 0)
        for level in (75, 90):
            if previous_risk < level <= risk_used:
                reasons.append(f"Risk used crossed {level}%")
        previous_giveback = float(signal.get("last_mfe_giveback") or 0)
        if mfe_giveback >= 1.0 and mfe_giveback - previous_giveback >= 0.75:
            reasons.append(f"MFE giveback increased to {mfe_giveback:.2f}%")

        positive = sorted(components.items(), key=lambda item: item[1], reverse=True)
        weak = sorted(components.items(), key=lambda item: item[1])
        sentences: list[str] = []
        if health_score >= 78:
            sentences.append("The trade remains technically healthy.")
        elif health_score >= 60:
            sentences.append("The setup remains valid, but follow-through is not yet decisive.")
        elif health_score >= 42:
            sentences.append("The trade is weakening and requires closer risk control.")
        else:
            sentences.append("The trade is at risk as price approaches invalidation.")
        sentences.append(f"{positive[0][0].capitalize()} is the strongest live component at {positive[0][1]:.0f}%.")
        if weak[0][1] < 50:
            sentences.append(f"{weak[0][0].capitalize()} is the main weakness at {weak[0][1]:.0f}%.")
        if risk_used >= 75:
            sentences.append(f"Risk consumption is elevated at {risk_used:.0f}%.")
        if mfe_giveback >= 1:
            sentences.append(f"Price has given back {mfe_giveback:.2f}% from maximum favorable excursion.")
        if current_r > 0:
            sentences.append("Price remains on the favorable side of the entry.")
        else:
            sentences.append("Price is currently trading against the entry.")

        return IntelligenceSnapshot(
            confidence=confidence,
            confidence_delta=confidence_delta,
            health=health,
            health_score=health_score,
            trend=round(components["trend"], 1),
            structure=round(components["structure"], 1),
            liquidity=round(components["liquidity"], 1),
            momentum=round(components["momentum"], 1),
            commentary=" ".join(sentences),
            alert_reasons=reasons,
            risk_used=round(risk_used, 1),
            distance_to_stop=round(distance_to_stop, 1),
            mfe_giveback=round(mfe_giveback, 2),
        )
