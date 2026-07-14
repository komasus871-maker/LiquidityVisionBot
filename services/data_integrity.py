from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class IntegrityResult:
    valid: bool
    code: str = "OK"
    reason: str = ""
    details: dict[str, Any] | None = None


class DataIntegrityEngine:
    """Rejects malformed plans and impossible live activations.

    The engine is intentionally conservative: bad data must never become a
    training example or an ACTIVE trade.
    """

    def __init__(self, *, max_activation_deviation_pct: float = 3.0, max_stale_minutes: int = 10):
        self.max_activation_deviation_pct = max(0.25, float(max_activation_deviation_pct))
        self.max_stale_minutes = max(1, int(max_stale_minutes))

    @staticmethod
    def validate_plan(analysis: dict[str, Any]) -> IntegrityResult:
        try:
            side = str(analysis.get("direction") or "").upper()
            entry = float(analysis.get("entry") or 0)
            stop = float(analysis.get("stop") or 0)
            targets = [float(analysis.get(k) or 0) for k in ("tp1", "tp2", "tp3")]
        except (TypeError, ValueError):
            return IntegrityResult(False, "NON_NUMERIC_PLAN", "Trade plan contains non-numeric prices")

        if side == "LONG":
            geometry = stop < entry < targets[0] < targets[1] < targets[2]
        elif side == "SHORT":
            geometry = targets[2] < targets[1] < targets[0] < entry < stop
        else:
            return IntegrityResult(False, "INVALID_SIDE", "Direction must be LONG or SHORT")
        if not geometry:
            return IntegrityResult(False, "INVALID_GEOMETRY", "Entry, stop and targets are not ordered correctly")

        low = analysis.get("preferred_entry_low")
        high = analysis.get("preferred_entry_high")
        if low is not None and high is not None:
            lo, hi = sorted((float(low), float(high)))
            if not lo <= entry <= hi:
                return IntegrityResult(
                    False,
                    "ENTRY_OUTSIDE_ZONE",
                    "Planned entry is outside its preferred entry zone",
                    {"entry": entry, "zone_low": lo, "zone_high": hi},
                )
        return IntegrityResult(True)

    def validate_activation(self, signal: dict[str, Any], activation_price: float) -> IntegrityResult:
        entry = float(signal.get("entry") or 0)
        if entry <= 0 or activation_price <= 0:
            return IntegrityResult(False, "INVALID_PRICE", "Entry or activation price is not positive")

        deviation_pct = abs(activation_price - entry) / entry * 100
        low = signal.get("preferred_entry_low")
        high = signal.get("preferred_entry_high")
        zone_width_pct = 0.0
        expanded_zone_ok = False
        if low is not None and high is not None:
            lo, hi = sorted((float(low), float(high)))
            width = max(hi - lo, entry * 0.001)
            zone_width_pct = width / entry * 100
            expanded_zone_ok = (lo - width * 0.5) <= activation_price <= (hi + width * 0.5)

        tolerance_pct = max(self.max_activation_deviation_pct, zone_width_pct * 1.5)
        if not expanded_zone_ok and deviation_pct > tolerance_pct:
            return IntegrityResult(
                False,
                "ACTIVATION_PRICE_DEVIATION",
                "Activation price is too far from the locked trade plan",
                {
                    "entry": entry,
                    "activation_price": activation_price,
                    "deviation_pct": round(deviation_pct, 4),
                    "tolerance_pct": round(tolerance_pct, 4),
                },
            )
        return IntegrityResult(True, details={"deviation_pct": deviation_pct, "tolerance_pct": tolerance_pct})

    def validate_market_frame(self, df: pd.DataFrame) -> IntegrityResult:
        required = {"open", "high", "low", "close"}
        if df is None or df.empty or not required.issubset(df.columns):
            return IntegrityResult(False, "MISSING_OHLC", "Market frame is empty or missing OHLC columns")
        if df[list(required)].isna().any().any():
            return IntegrityResult(False, "NAN_OHLC", "Market frame contains missing OHLC values")
        if df.index.has_duplicates:
            return IntegrityResult(False, "DUPLICATE_CANDLES", "Market frame contains duplicate candle timestamps")
        invalid = (df["high"] < df[["open", "close", "low"]].max(axis=1)) | (df["low"] > df[["open", "close", "high"]].min(axis=1))
        if bool(invalid.any()):
            return IntegrityResult(False, "INVALID_OHLC", "Market frame contains impossible candle geometry")

        if isinstance(df.index, pd.DatetimeIndex) and len(df.index):
            last = df.index[-1]
            if last.tzinfo is None:
                last = last.tz_localize("UTC")
            age_minutes = (datetime.now(timezone.utc) - last.to_pydatetime()).total_seconds() / 60
            if age_minutes > self.max_stale_minutes:
                return IntegrityResult(False, "STALE_MARKET_DATA", "Latest market candle is stale", {"age_minutes": age_minutes})
        return IntegrityResult(True)
