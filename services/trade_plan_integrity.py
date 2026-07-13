from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


class InvalidTradePlan(ValueError):
    pass


@dataclass(frozen=True)
class TradePlan:
    side: str
    current_price: float
    entry: float
    entry_low: float
    entry_high: float
    stop: float
    tp1: float
    tp2: float
    tp3: float
    rr: float
    entry_type: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TradePlanIntegrity:
    """Build and validate an executable plan around the planned fill, not spot price."""

    @staticmethod
    def validate(side: str, entry: float, stop: float, tp1: float, tp2: float, tp3: float) -> None:
        values = (entry, stop, tp1, tp2, tp3)
        if any(not isinstance(v, (int, float)) or v <= 0 for v in values):
            raise InvalidTradePlan("Trade plan contains a non-positive or non-numeric price")
        if side == "LONG":
            valid = stop < entry < tp1 < tp2 < tp3
        elif side == "SHORT":
            valid = tp3 < tp2 < tp1 < entry < stop
        else:
            raise InvalidTradePlan(f"Unsupported side: {side}")
        if not valid:
            raise InvalidTradePlan(
                f"Invalid {side} geometry: stop={stop}, entry={entry}, "
                f"tp1={tp1}, tp2={tp2}, tp3={tp3}"
            )

    @classmethod
    def build(cls, analysis: dict[str, Any]) -> TradePlan:
        side = str(analysis.get("direction") or "").upper()
        current = float(analysis.get("price") or analysis.get("entry") or 0)
        low = float(analysis.get("preferred_entry_low") or current)
        high = float(analysis.get("preferred_entry_high") or current)
        low, high = sorted((low, high))
        status = str(analysis.get("execution_status") or "")

        planned = status != "🟢 READY" and abs(high - low) > max(current * 1e-8, 1e-12)
        entry = (low + high) / 2.0 if planned else current
        atr = float((analysis.get("atr") or {}).get("atr") or 0)
        raw_distance = abs(float(analysis.get("stop") or current) - current)
        risk = max(raw_distance, atr * 0.65, entry * 0.0025)
        buffer = max(atr * 0.12, entry * 0.0005)

        if side == "LONG":
            stop = min(float(analysis.get("stop") or entry - risk), low - buffer, entry - risk)
            risk = entry - stop
            tp1, tp2, tp3 = entry + risk, entry + risk * 2, entry + risk * 3
        elif side == "SHORT":
            stop = max(float(analysis.get("stop") or entry + risk), high + buffer, entry + risk)
            risk = stop - entry
            tp1, tp2, tp3 = entry - risk, entry - risk * 2, entry - risk * 3
        else:
            raise InvalidTradePlan(f"Unsupported side: {side}")

        # Extremely low-priced assets can produce a mathematically impossible TP3.
        if min(tp1, tp2, tp3) <= 0:
            raise InvalidTradePlan("Targets cross zero; the proposed risk distance is not executable")

        rr = 3.0
        cls.validate(side, entry, stop, tp1, tp2, tp3)
        return TradePlan(
            side=side,
            current_price=current,
            entry=round(entry, 12),
            entry_low=round(low, 12),
            entry_high=round(high, 12),
            stop=round(stop, 12),
            tp1=round(tp1, 12),
            tp2=round(tp2, 12),
            tp3=round(tp3, 12),
            rr=rr,
            entry_type="PLANNED_ZONE" if planned else "MARKET_READY",
        )

    @classmethod
    def apply(cls, analysis: dict[str, Any]) -> dict[str, Any]:
        plan = cls.build(analysis)
        analysis.update(
            {
                "current_price": plan.current_price,
                "entry": plan.entry,
                "planned_entry": plan.entry,
                "preferred_entry_low": plan.entry_low,
                "preferred_entry_high": plan.entry_high,
                "stop": plan.stop,
                "tp1": plan.tp1,
                "tp2": plan.tp2,
                "tp3": plan.tp3,
                "rr": plan.rr,
                "entry_type": plan.entry_type,
                "plan_valid": True,
                "plan_error": None,
            }
        )
        return analysis
