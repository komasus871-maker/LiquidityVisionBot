from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from utils.timeframe import normalize_market_timeframe, timeframe_seconds


@dataclass(frozen=True)
class IntegrityResult:
    valid: bool
    code: str = "OK"
    reason: str = ""
    details: dict[str, Any] | None = None


@dataclass(frozen=True)
class MarketFrameResult:
    """The single normalized candle-boundary result used before analysis."""
    frame: pd.DataFrame
    valid: bool
    status: str
    code: str = "OK"
    reason: str = ""
    details: dict[str, Any] | None = None

    def quality(self) -> dict[str, Any]:
        result = {
            "status": self.status,
            "code": self.code,
            "reason": self.reason,
            "valid": self.valid,
            "contract_version": "market-data-v1",
        }
        result.update(self.details or {})
        return result


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
        timeframe = (getattr(df, "attrs", {}) or {}).get("market_data_timeframe") or "1m"
        result = self.prepare_market_frame(
            df, timeframe=timeframe, minimum_history=1,
            require_freshness=bool((getattr(df, "attrs", {}) or {}).get("market_data_provider")),
        )
        return IntegrityResult(result.valid, result.code, result.reason, result.quality())

    @staticmethod
    def provider_error_frame(*, provider: str, symbol: str, timeframe: str, reason: str) -> pd.DataFrame:
        """Represent public-provider failure explicitly, never as ordinary empty candles."""
        frame = pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume", "confirm"])
        frame.attrs.update({
            "market_data_provider": provider,
            "market_data_symbol": symbol,
            "market_data_timeframe": timeframe,
            "market_data_closed_semantics": "UNKNOWN",
            "market_data_provider_error": reason,
            "market_data_fallback_used": False,
        })
        return frame

    @staticmethod
    def _timestamp_column(frame: pd.DataFrame) -> str | None:
        for column in ("time", "timestamp", "datetime", "open_time", "ts"):
            if column in frame.columns:
                return column
        return None

    @staticmethod
    def _utc_series(values: pd.Series) -> pd.Series:
        if pd.api.types.is_numeric_dtype(values):
            non_null = values.dropna()
            maximum = float(non_null.abs().max()) if not non_null.empty else 0.0
            unit = "ms" if maximum > 10_000_000_000 else "s"
            return pd.to_datetime(values, unit=unit, utc=True, errors="coerce")
        return pd.to_datetime(values, utc=True, errors="coerce")

    @staticmethod
    def _result(frame: pd.DataFrame, valid: bool, status: str, code: str, reason: str, details: dict[str, Any]) -> MarketFrameResult:
        result = MarketFrameResult(frame=frame, valid=valid, status=status, code=code, reason=reason, details=details)
        frame.attrs["market_data_quality"] = result.quality()
        return result

    def prepare_market_frame(
        self,
        dataframe: Any,
        *,
        timeframe: Any,
        minimum_history: int = 1,
        reference_time: datetime | pd.Timestamp | None = None,
        require_freshness: bool = False,
        require_time_integrity: bool | None = None,
        provider: str | None = None,
    ) -> MarketFrameResult:
        """Validate and normalize one candle sequence before it reaches analysis.

        This method deliberately only normalizes unambiguous order and exact
        duplicates; every financially meaningful contradiction fails closed.
        """
        canonical_timeframe = normalize_market_timeframe(timeframe)
        if not isinstance(dataframe, pd.DataFrame):
            return self._result(pd.DataFrame(), False, "UNKNOWN", "DATAFRAME_MISSING", "Market data is not a DataFrame", {})
        frame = dataframe.copy(deep=True)
        attrs = dict(getattr(dataframe, "attrs", {}) or {})
        frame.attrs.update(attrs)
        provider = provider or attrs.get("market_data_provider") or attrs.get("exchange")
        details: dict[str, Any] = {
            "provider": provider or "UNKNOWN",
            "symbol": attrs.get("market_data_symbol") or attrs.get("symbol"),
            "timeframe": canonical_timeframe or str(timeframe or "UNKNOWN"),
            "requested_limit": attrs.get("market_data_requested_limit"),
            "fallback_used": bool(attrs.get("market_data_fallback_used", False)),
            "source_candles": len(frame),
            "minimum_history": int(minimum_history),
        }
        if attrs.get("market_data_provider_error"):
            return self._result(frame, False, "PROVIDER_ERROR", "PROVIDER_ERROR", str(attrs["market_data_provider_error"]), details)
        if canonical_timeframe is None:
            return self._result(frame, False, "UNKNOWN", "UNKNOWN_TIMEFRAME", "Candle timeframe is unsupported or missing", details)
        duration = timeframe_seconds(canonical_timeframe)
        assert duration is not None
        details["timeframe_seconds"] = duration
        required = ("open", "high", "low", "close", "volume")
        missing = [column for column in required if column not in frame.columns]
        if frame.empty:
            return self._result(frame, False, "INSUFFICIENT", "EMPTY_MARKET_DATA", "Provider returned no candles", details)
        if missing:
            details["missing_columns"] = missing
            return self._result(frame, False, "INCOMPLETE", "MISSING_OHLCV", "Market frame is missing required OHLCV columns", details)

        # Historical replay needs timestamp, gap, order, and future checks but
        # must not call an old dataset stale relative to today's clock. Runtime
        # callers retain the stricter freshness check by default.
        require_time_integrity = require_freshness if require_time_integrity is None else bool(require_time_integrity)

        timestamp_column = self._timestamp_column(frame)
        if timestamp_column is None:
            if require_time_integrity:
                return self._result(frame, False, "INCOMPLETE", "MISSING_TIMESTAMP", "Timestamped candle time is required for this evaluation", details)
            # Direct research/unit fixtures may omit timestamps; they never gain a
            # freshness guarantee and cannot represent a provider runtime path.
            details["timestamp_semantics"] = "UNVERIFIED_DIRECT_INPUT"
            timestamps = None
        else:
            timestamps = self._utc_series(frame[timestamp_column])
            if timestamps.isna().any():
                details["timestamp_column"] = timestamp_column
                return self._result(frame, False, "INCOMPLETE", "MALFORMED_TIMESTAMP", "A candle timestamp could not be normalized to UTC", details)
            frame["time"] = timestamps
            details["timestamp_column"] = timestamp_column
            details["timestamp_semantics"] = "UTC_OPEN_TIME"

        for column in required:
            converted = pd.to_numeric(frame[column], errors="coerce")
            if converted.isna().any() or not np.isfinite(converted.to_numpy(dtype=float)).all():
                details["invalid_column"] = column
                return self._result(frame, False, "INCOMPLETE", "NON_FINITE_OHLCV", "Market frame contains non-finite OHLCV values", details)
            frame[column] = converted.astype(float)
        if (frame[["open", "high", "low", "close"]] < 0).any().any():
            return self._result(frame, False, "INVALID", "NEGATIVE_PRICE", "Market frame contains a negative price", details)
        if (frame["volume"] < 0).any():
            return self._result(frame, False, "INVALID", "NEGATIVE_VOLUME", "Market frame contains negative volume", details)
        invalid_geometry = (
            (frame["high"] < frame[["open", "close", "low"]].max(axis=1))
            | (frame["low"] > frame[["open", "close", "high"]].min(axis=1))
            | (frame["high"] < frame["low"])
        )
        if bool(invalid_geometry.any()):
            return self._result(frame, False, "INVALID", "INVALID_OHLC", "Market frame contains impossible candle geometry", details)

        removed_forming = 0
        if "confirm" in frame.columns:
            closed = frame["confirm"].astype(str).str.strip().str.lower().isin({"1", "true", "yes", "closed"})
            removed_forming = int((~closed).sum())
            frame = frame.loc[closed].copy()
            frame.attrs.update(attrs)
            details["closed_candle_semantics"] = "CLOSED_ONLY"
        else:
            details["closed_candle_semantics"] = attrs.get("market_data_closed_semantics") or "CLOSED_ONLY_ASSUMED"
        details["forming_candles_removed"] = removed_forming
        if frame.empty:
            return self._result(frame, False, "INSUFFICIENT", "NO_CLOSED_CANDLES", "No closed candles remain after filtering", details)

        if timestamps is not None:
            # `time` is already UTC and aligns with the retained rows.
            duplicate_mask = frame.duplicated(subset=["time"], keep=False)
            if bool(duplicate_mask.any()):
                duplicate_rows = frame.loc[duplicate_mask, ["time", *required]]
                conflicts = duplicate_rows.groupby("time", sort=False)[list(required)].nunique(dropna=False).gt(1).any(axis=1)
                if bool(conflicts.any()):
                    details["conflicting_timestamps"] = [value.isoformat() for value in conflicts[conflicts].index[:5]]
                    return self._result(frame, False, "CONFLICTING", "CONFLICTING_DUPLICATE", "Same timestamp has conflicting OHLCV values", details)
                duplicate_count = int(duplicate_mask.sum() - duplicate_rows["time"].nunique())
                frame = frame.drop_duplicates(subset=["time"], keep="first").copy()
                frame.attrs.update(attrs)
                details["exact_duplicates_removed"] = duplicate_count
            original_order = frame["time"].is_monotonic_increasing
            if not original_order:
                frame = frame.sort_values("time", kind="mergesort").reset_index(drop=True)
                frame.attrs.update(attrs)
                details["ordering"] = "OUT_OF_ORDER_NORMALIZED"
            else:
                details["ordering"] = "CHRONOLOGICAL"

            first, last = frame["time"].iloc[0], frame["time"].iloc[-1]
            details.update({"first_candle_at": first.isoformat(), "last_candle_at": last.isoformat()})
            if require_time_integrity:
                reference = pd.Timestamp(reference_time or datetime.now(timezone.utc))
                if reference.tzinfo is None:
                    reference = reference.tz_localize("UTC")
                else:
                    reference = reference.tz_convert("UTC")
                details["reference_at"] = reference.isoformat()
                if bool((frame["time"] > reference).any()):
                    return self._result(frame, False, "INVALID", "FUTURE_CANDLE", "Market frame contains an open timestamp in the future", details)
                diffs = frame["time"].diff().dropna().dt.total_seconds()
                gaps = diffs[diffs > duration]
                if not gaps.empty:
                    details["gap_count"] = int(len(gaps))
                    details["missing_candle_count"] = int(sum(max(1, round(value / duration) - 1) for value in gaps))
                    details["gap_after"] = [
                        (frame.loc[index, "time"] - pd.Timedelta(seconds=duration)).isoformat()
                        for index in gaps.index[:5]
                    ]
                    return self._result(frame, False, "GAPPED", "MISSING_CANDLES", "Market frame has missing expected candles", details)
                if not diffs.empty and bool((diffs != duration).any()):
                    return self._result(frame, False, "GAPPED", "IRREGULAR_SPACING", "Market frame does not use consistent timeframe spacing", details)
                # Last open + interval is the expected close; 1.25 candles allows
                # normal provider publication lag without applying a global minute rule.
                expected_close = last + pd.Timedelta(seconds=duration)
                tolerance_seconds = duration * 1.25
                age_seconds = (reference - expected_close).total_seconds()
                details.update({"expected_latest_close_at": expected_close.isoformat(), "freshness_tolerance_seconds": tolerance_seconds, "age_after_expected_close_seconds": age_seconds})
                if require_freshness and age_seconds > tolerance_seconds:
                    return self._result(frame, False, "STALE", "STALE_MARKET_DATA", "Latest closed candle is older than its timeframe allowance", details)
            else:
                details["timing_validation"] = "UNVERIFIED_DIRECT_INPUT"
        else:
            details["closed_candle_semantics"] = "UNVERIFIED_DIRECT_INPUT"

        if len(frame) < max(1, int(minimum_history)):
            details["usable_candles"] = len(frame)
            return self._result(frame, False, "INSUFFICIENT", "INSUFFICIENT_HISTORY", "Too few closed candles for this analysis", details)
        details["usable_candles"] = len(frame)
        return self._result(frame, True, "VALID", "OK", "Candle contract satisfied", details)
