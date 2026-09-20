"""Research-only derivatives truth, alignment, and positioning primitives.

This module deliberately has no exchange client, scheduler, execution backend,
or production-strategy import.  It can describe historical state and construct
immutable research identities; it cannot authorize or submit an order.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd


SCHEMA_VERSION = "derivatives-alpha-v1"
FEATURE_SCHEMA_VERSION = "positioning-state-v1"
PREREGISTRATION_ID = "derivatives-alpha-primary-1h-v1"


class DerivativesIntegrityStatus(str, Enum):
    VALID = "VALID"
    STALE = "STALE"
    GAPPED = "GAPPED"
    INSUFFICIENT = "INSUFFICIENT"
    CONFLICTING = "CONFLICTING"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class DerivativesIntegrityResult:
    status: DerivativesIntegrityStatus
    code: str
    rows: int
    start: str | None
    end: str | None
    largest_gap_seconds: float | None = None

    @property
    def valid(self) -> bool:
        return self.status is DerivativesIntegrityStatus.VALID


@dataclass(frozen=True)
class DerivativesCandidateSpec:
    family: str
    direction: str
    variant: str
    parameters: Mapping[str, float]

    @property
    def candidate_id(self) -> str:
        payload = {
            "schema": SCHEMA_VERSION,
            "preregistration": PREREGISTRATION_ID,
            "family": self.family,
            "direction": self.direction,
            "variant": self.variant,
            "parameters": dict(self.parameters),
            "entry": "NEXT_OPEN_MARKET",
            "stop_atr": 1.5,
            "target_r": 1.5,
            "max_holding_bars": 24,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:16]


DERIV_DEV_START = pd.Timestamp("2024-07-01T00:00:00Z")
DERIV_DEV_END = pd.Timestamp("2025-09-30T23:00:00Z")
DERIV_VALIDATION_START = pd.Timestamp("2025-10-01T00:00:00Z")
DERIV_VALIDATION_END = pd.Timestamp("2026-02-28T23:00:00Z")
DERIV_BLIND_START = pd.Timestamp("2026-03-01T00:00:00Z")
DERIV_BLIND_END = pd.Timestamp("2026-06-30T23:00:00Z")


def stable_hash(value: Any, length: int = 16) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()[:length]


def causal_asof(
    decisions: pd.DataFrame,
    observations: pd.DataFrame,
    *,
    decision_column: str = "decision_at",
    source_column: str = "source_at",
    availability_column: str = "available_at",
    maximum_age: pd.Timedelta,
    prefix: str,
) -> pd.DataFrame:
    """Backward-only as-of alignment using *availability*, never event proximity."""
    left = decisions.copy()
    right = observations.copy()
    left[decision_column] = pd.to_datetime(left[decision_column], utc=True, errors="coerce")
    right[source_column] = pd.to_datetime(right[source_column], utc=True, errors="coerce")
    right[availability_column] = pd.to_datetime(right[availability_column], utc=True, errors="coerce")
    if left[decision_column].isna().any() or right[[source_column, availability_column]].isna().any().any():
        raise ValueError("alignment timestamps must be valid UTC timestamps")
    if (right[availability_column] < right[source_column]).any():
        raise ValueError("availability cannot precede the source timestamp")
    if right[availability_column].duplicated().any():
        raise ValueError("duplicate observation availability timestamps are conflicting")
    payload_columns = [c for c in right.columns if c not in {source_column, availability_column}]
    rename = {c: f"{prefix}_{c}" for c in payload_columns}
    right = right.rename(columns=rename).sort_values(availability_column)
    right = right.rename(columns={source_column: f"{prefix}_source_at", availability_column: f"{prefix}_available_at"})
    merged = pd.merge_asof(
        left.sort_values(decision_column), right,
        left_on=decision_column, right_on=f"{prefix}_available_at", direction="backward",
        tolerance=maximum_age, allow_exact_matches=True,
    )
    merged[f"{prefix}_age_seconds"] = (
        merged[decision_column] - merged[f"{prefix}_available_at"]
    ).dt.total_seconds()
    if (merged[f"{prefix}_available_at"] > merged[decision_column]).fillna(False).any():
        raise AssertionError("future observation admitted by causal alignment")
    return merged


def validate_derivatives_frame(
    frame: pd.DataFrame,
    *,
    timestamp_column: str,
    numeric_columns: Sequence[str],
    expected_frequency: pd.Timedelta,
    maximum_gap: pd.Timedelta,
    minimum_rows: int = 2,
    nonnegative_columns: Sequence[str] = (),
) -> DerivativesIntegrityResult:
    if frame is None:
        return DerivativesIntegrityResult(DerivativesIntegrityStatus.PROVIDER_ERROR, "NO_FRAME", 0, None, None)
    if timestamp_column not in frame or any(column not in frame for column in numeric_columns):
        return DerivativesIntegrityResult(DerivativesIntegrityStatus.UNKNOWN, "SCHEMA_MISMATCH", len(frame), None, None)
    data = frame.copy()
    ts = pd.to_datetime(data[timestamp_column], utc=True, errors="coerce")
    if len(data) < minimum_rows:
        return DerivativesIntegrityResult(DerivativesIntegrityStatus.INSUFFICIENT, "TOO_FEW_ROWS", len(data), None, None)
    if ts.isna().any() or not ts.is_monotonic_increasing:
        return DerivativesIntegrityResult(DerivativesIntegrityStatus.CONFLICTING, "INVALID_CHRONOLOGY", len(data), None, None)
    if ts.duplicated().any():
        return DerivativesIntegrityResult(DerivativesIntegrityStatus.CONFLICTING, "DUPLICATE_TIMESTAMP", len(data), None, None)
    values = data[list(numeric_columns)].apply(pd.to_numeric, errors="coerce")
    if values.isna().any().any() or not np.isfinite(values.to_numpy(dtype=float)).all():
        return DerivativesIntegrityResult(DerivativesIntegrityStatus.CONFLICTING, "NONFINITE_VALUE", len(data), None, None)
    if any((values[column] < 0).any() for column in nonnegative_columns):
        return DerivativesIntegrityResult(DerivativesIntegrityStatus.CONFLICTING, "IMPOSSIBLE_NEGATIVE_VALUE", len(data), None, None)
    gaps = ts.diff().dropna()
    largest = float(gaps.max().total_seconds()) if not gaps.empty else 0.0
    start, end = ts.iloc[0].isoformat(), ts.iloc[-1].isoformat()
    if not gaps.empty and gaps.max() > maximum_gap:
        return DerivativesIntegrityResult(DerivativesIntegrityStatus.GAPPED, "UNEXPECTED_GAP", len(data), start, end, largest)
    if not gaps.empty and (gaps < expected_frequency).any():
        return DerivativesIntegrityResult(DerivativesIntegrityStatus.CONFLICTING, "FREQUENCY_CONFLICT", len(data), start, end, largest)
    return DerivativesIntegrityResult(DerivativesIntegrityStatus.VALID, "OK", len(data), start, end, largest)


def _rolling_percentile(series: pd.Series, window: int, minimum: int) -> pd.Series:
    return series.rolling(window, min_periods=minimum).apply(
        lambda values: float(pd.Series(values).rank(pct=True).iloc[-1]), raw=False,
    )


class PositioningStateEngine:
    """Causal descriptors only; this class has no execution authority."""

    def __init__(self, lookback: int = 24 * 30, minimum_history: int = 24 * 7):
        if lookback < minimum_history or minimum_history < 24:
            raise ValueError("positioning history must include at least 24 observations")
        self.lookback = lookback
        self.minimum_history = minimum_history

    def transform(self, frame: pd.DataFrame) -> pd.DataFrame:
        required = {"decision_at", "close", "oi_usd", "funding_rate", "basis_pct"}
        if not required.issubset(frame):
            raise ValueError(f"missing derivatives fields: {sorted(required - set(frame.columns))}")
        result = frame.copy()
        result["decision_at"] = pd.to_datetime(result["decision_at"], utc=True, errors="raise")
        if not result["decision_at"].is_monotonic_increasing or result["decision_at"].duplicated().any():
            raise ValueError("positioning input must be unique chronological decisions")
        for column in ("close", "oi_usd", "funding_rate", "basis_pct"):
            result[column] = pd.to_numeric(result[column], errors="raise")
            if not np.isfinite(result[column].to_numpy(dtype=float)).all():
                raise ValueError(f"{column} must be finite")
        result["price_return_1h"] = result["close"].pct_change()
        result["price_return_6h"] = result["close"].pct_change(6)
        result["oi_change_1h"] = result["oi_usd"].pct_change()
        result["oi_change_3h"] = result["oi_usd"].pct_change(3)
        result["oi_change_6h"] = result["oi_usd"].pct_change(6)
        result["price_return_6h_percentile"] = _rolling_percentile(
            result["price_return_6h"], self.lookback, self.minimum_history,
        )
        result["oi_change_6h_percentile"] = _rolling_percentile(
            result["oi_change_6h"], self.lookback, self.minimum_history,
        )
        oi_mean = result["oi_usd"].rolling(self.lookback, min_periods=self.minimum_history).mean()
        oi_std = result["oi_usd"].rolling(self.lookback, min_periods=self.minimum_history).std()
        result["oi_zscore"] = (result["oi_usd"] - oi_mean) / oi_std.replace(0, np.nan)
        result["oi_percentile"] = _rolling_percentile(result["oi_usd"], self.lookback, self.minimum_history)
        result["funding_change"] = result["funding_rate"].diff()
        result["funding_percentile"] = _rolling_percentile(result["funding_rate"], self.lookback, self.minimum_history)
        funding_mean = result["funding_rate"].rolling(self.lookback, min_periods=self.minimum_history).mean()
        funding_std = result["funding_rate"].rolling(self.lookback, min_periods=self.minimum_history).std()
        result["funding_zscore"] = (result["funding_rate"] - funding_mean) / funding_std.replace(0, np.nan)
        result["basis_change"] = result["basis_pct"].diff()
        result["basis_percentile"] = _rolling_percentile(result["basis_pct"], self.lookback, self.minimum_history)
        basis_mean = result["basis_pct"].rolling(self.lookback, min_periods=self.minimum_history).mean()
        basis_std = result["basis_pct"].rolling(self.lookback, min_periods=self.minimum_history).std()
        result["basis_zscore"] = (result["basis_pct"] - basis_mean) / basis_std.replace(0, np.nan)
        result["price_oi_interaction"] = result["price_return_6h"] * result["oi_change_6h"]
        result["price_oi_quadrant"] = np.select(
            [
                (result["price_return_6h"] > 0) & (result["oi_change_6h"] > 0),
                (result["price_return_6h"] > 0) & (result["oi_change_6h"] <= 0),
                (result["price_return_6h"] <= 0) & (result["oi_change_6h"] > 0),
                (result["price_return_6h"] <= 0) & (result["oi_change_6h"] <= 0),
            ],
            ["PRICE_UP_OI_UP", "PRICE_UP_OI_DOWN", "PRICE_DOWN_OI_UP", "PRICE_DOWN_OI_DOWN"],
            default="UNKNOWN",
        )
        ready = result[["funding_percentile", "basis_percentile", "oi_change_6h"]].notna().all(axis=1)
        result["positioning_state"] = "INSUFFICIENT"
        result.loc[ready, "positioning_state"] = "NEUTRAL"
        result.loc[ready & (result["funding_percentile"] >= .8) & (result["basis_percentile"] >= .7), "positioning_state"] = "LONG_CROWDING"
        result.loc[ready & (result["funding_percentile"] <= .2) & (result["basis_percentile"] <= .3), "positioning_state"] = "SHORT_CROWDING"
        result.loc[ready & (result["oi_change_6h"] > 0) & (result["price_return_6h"] > 0), "positioning_state"] = "LEVERAGE_EXPANSION_LONG"
        result.loc[ready & (result["oi_change_6h"] > 0) & (result["price_return_6h"] < 0), "positioning_state"] = "LEVERAGE_EXPANSION_SHORT"
        result.loc[ready & (result["oi_change_6h"] <= 0) & (result["price_return_6h"] > 0), "positioning_state"] = "SHORT_COVERING"
        result.loc[ready & (result["oi_change_6h"] <= 0) & (result["price_return_6h"] < 0), "positioning_state"] = "LONG_LIQUIDATION"
        return result


def frozen_candidates() -> tuple[DerivativesCandidateSpec, ...]:
    """Exactly twelve preregistered variants: four mechanisms, three variants."""
    specs: list[DerivativesCandidateSpec] = []
    variants = (("BROAD", .60, .80), ("BASE", .70, .85), ("STRICT", .80, .90))
    for name, core, extreme in variants:
        specs.append(DerivativesCandidateSpec("D1_LEVERAGED_TREND_LONG", "LONG", name, {"core": core, "extreme": extreme}))
        specs.append(DerivativesCandidateSpec("D1_LEVERAGED_TREND_SHORT", "SHORT", name, {"core": core, "extreme": extreme}))
        specs.append(DerivativesCandidateSpec("D2_DELEVERAGING_REVERSAL", "BOTH", name, {"core": core, "extreme": extreme}))
        specs.append(DerivativesCandidateSpec("D3_CROWDING_REVERSAL", "BOTH", name, {"core": core, "extreme": extreme}))
    return tuple(specs)


def realized_funding_r(
    settlements: pd.DataFrame, *, fill_at: Any, exit_at: Any, direction: str, entry: float, risk: float,
) -> float:
    """Return directional realized funding cost in R; feature usage is not counted here."""
    if risk <= 0 or entry <= 0 or direction not in {"LONG", "SHORT"}:
        raise ValueError("valid geometry and direction are required")
    data = settlements.copy()
    data["funding_at"] = pd.to_datetime(data["funding_at"], utc=True, errors="raise")
    start, end = pd.Timestamp(fill_at), pd.Timestamp(exit_at)
    start = start.tz_localize("UTC") if start.tzinfo is None else start.tz_convert("UTC")
    end = end.tz_localize("UTC") if end.tzinfo is None else end.tz_convert("UTC")
    charged = data.loc[(data["funding_at"] > start) & (data["funding_at"] <= end), "funding_rate"].astype(float).sum()
    signed_cost = charged if direction == "LONG" else -charged
    return float(entry * signed_cost / risk)


def candidate_registry_identity(specs: Sequence[DerivativesCandidateSpec]) -> str:
    return stable_hash([asdict(spec) | {"candidate_id": spec.candidate_id} for spec in specs])
