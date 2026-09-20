"""Research-only genuine aggressor-flow primitives and causal feature engine."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

import numpy as np
import pandas as pd


FLOW_SCHEMA = "flow-microstructure-v1"


class FlowIntegrityStatus(str, Enum):
    VALID = "VALID"
    STALE = "STALE"
    GAPPED = "GAPPED"
    INSUFFICIENT = "INSUFFICIENT"
    CONFLICTING = "CONFLICTING"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class FlowIntegrityResult:
    status: FlowIntegrityStatus
    code: str
    rows: int

    @property
    def valid(self) -> bool:
        return self.status is FlowIntegrityStatus.VALID


def stable_hash(value: Any, length: int = 16) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()[:length]


def validate_flow_frame(frame: pd.DataFrame, expected_frequency: str = "5min") -> FlowIntegrityResult:
    required = {
        "time", "open", "high", "low", "close", "volume", "quote_volume",
        "trade_count", "taker_buy_volume", "taker_buy_quote_volume",
    }
    if frame is None or not required.issubset(frame):
        return FlowIntegrityResult(FlowIntegrityStatus.UNKNOWN, "SCHEMA_MISMATCH", 0 if frame is None else len(frame))
    data = frame.copy()
    data["time"] = pd.to_datetime(data["time"], utc=True, errors="coerce")
    if len(data) < 2:
        return FlowIntegrityResult(FlowIntegrityStatus.INSUFFICIENT, "TOO_FEW_ROWS", len(data))
    if data["time"].isna().any() or not data["time"].is_monotonic_increasing or data["time"].duplicated().any():
        return FlowIntegrityResult(FlowIntegrityStatus.CONFLICTING, "CHRONOLOGY", len(data))
    numeric = list(required - {"time"})
    values = data[numeric].apply(pd.to_numeric, errors="coerce")
    if values.isna().any().any() or not np.isfinite(values.to_numpy(float)).all():
        return FlowIntegrityResult(FlowIntegrityStatus.CONFLICTING, "NONFINITE", len(data))
    nonnegative = ["open", "high", "low", "close", "volume", "quote_volume", "trade_count", "taker_buy_volume", "taker_buy_quote_volume"]
    if (values[nonnegative] < 0).any().any():
        return FlowIntegrityResult(FlowIntegrityStatus.CONFLICTING, "NEGATIVE", len(data))
    tolerance = 1e-8
    if (values["taker_buy_volume"] > values["volume"] * (1 + tolerance)).any() or (
        values["taker_buy_quote_volume"] > values["quote_volume"] * (1 + tolerance)
    ).any():
        return FlowIntegrityResult(FlowIntegrityStatus.CONFLICTING, "TAKER_BUY_EXCEEDS_TOTAL", len(data))
    gaps = data["time"].diff().dropna()
    expected = pd.Timedelta(expected_frequency)
    if (gaps > expected).any():
        return FlowIntegrityResult(FlowIntegrityStatus.GAPPED, "UNEXPECTED_GAP", len(data))
    if (gaps < expected).any():
        return FlowIntegrityResult(FlowIntegrityStatus.CONFLICTING, "UNEXPECTED_FREQUENCY", len(data))
    return FlowIntegrityResult(FlowIntegrityStatus.VALID, "OK", len(data))


def attach_aggressor_flow(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for column in ("volume", "quote_volume", "taker_buy_volume", "taker_buy_quote_volume"):
        result[column] = pd.to_numeric(result[column], errors="raise")
    result["taker_sell_volume"] = result["volume"] - result["taker_buy_volume"]
    result["taker_sell_quote_volume"] = result["quote_volume"] - result["taker_buy_quote_volume"]
    result["delta_base"] = result["taker_buy_volume"] - result["taker_sell_volume"]
    result["delta_quote"] = result["taker_buy_quote_volume"] - result["taker_sell_quote_volume"]
    result["normalized_delta"] = result["delta_quote"] / result["quote_volume"].replace(0, np.nan)
    result["cvd_quote"] = result["delta_quote"].fillna(0.0).cumsum()
    return result


def aggregate_completed(frame: pd.DataFrame, bars: int) -> pd.DataFrame:
    """Aggregate fixed groups of completed 5m intervals; incomplete groups vanish."""
    if bars < 1:
        raise ValueError("bars must be positive")
    data = attach_aggressor_flow(frame).copy()
    data["time"] = pd.to_datetime(data["time"], utc=True, errors="raise")
    frequency = f"{bars * 5}min"
    indexed = data.set_index("time")
    aggregation = {
        "open": "first", "high": "max", "low": "min", "close": "last",
        "volume": "sum", "quote_volume": "sum", "trade_count": "sum",
        "taker_buy_volume": "sum", "taker_buy_quote_volume": "sum",
        "oi_usd": "last", "oi_status": "last", "oi_available_at": "last",
        "oi_age_seconds": "last", "funding_rate": "last", "funding_status": "last",
        "funding_at": "last", "funding_available_at": "last",
        "provider": "last", "provider_symbol": "last",
    }
    aggregation = {key: value for key, value in aggregation.items() if key in indexed.columns}
    result = indexed.resample(frequency, origin="epoch", label="left", closed="left").agg(aggregation)
    counts = indexed["close"].resample(frequency, origin="epoch", label="left", closed="left").count()
    result = result.loc[counts == bars].reset_index()
    result["decision_at"] = result["time"] + pd.Timedelta(minutes=bars * 5)
    return attach_aggressor_flow(result)


def _rolling_percentile(series: pd.Series, window: int, minimum: int) -> pd.Series:
    return series.rolling(window, min_periods=minimum).rank(pct=True)


class FlowFeatureEngine:
    def __init__(self, *, bars_per_hour: int = 12):
        if bars_per_hour not in {4, 12}:
            raise ValueError("supported tactical frames are 5m and 15m")
        self.bars_per_hour = bars_per_hour

    def transform(self, frame: pd.DataFrame) -> pd.DataFrame:
        report = validate_flow_frame(frame, "5min" if self.bars_per_hour == 12 else "15min")
        if not report.valid:
            raise ValueError(f"flow integrity failed: {report.status}/{report.code}")
        result = attach_aggressor_flow(frame)
        result["time"] = pd.to_datetime(result["time"], utc=True)
        if "decision_at" not in result:
            minutes = 60 // self.bars_per_hour
            result["decision_at"] = result["time"] + pd.Timedelta(minutes=minutes)
        close = pd.to_numeric(result["close"])
        previous = close.shift(1)
        tr = pd.concat([
            pd.to_numeric(result["high"]) - pd.to_numeric(result["low"]),
            (pd.to_numeric(result["high"]) - previous).abs(),
            (pd.to_numeric(result["low"]) - previous).abs(),
        ], axis=1).max(axis=1)
        result["atr14"] = tr.rolling(14, min_periods=14).mean()
        for bars in (1, 3, 6, 12):
            result[f"return_{bars}bar"] = close.pct_change(bars, fill_method=None)
            result[f"delta_{bars}bar"] = result["delta_quote"].rolling(bars, min_periods=bars).sum()
            volume = result["quote_volume"].rolling(bars, min_periods=bars).sum()
            result[f"normalized_delta_{bars}bar"] = result[f"delta_{bars}bar"] / volume.replace(0, np.nan)
        one_day = self.bars_per_hour * 24
        week = one_day * 7
        result["delta_percentile"] = _rolling_percentile(result["normalized_delta_3bar"], week, one_day)
        mean = result["normalized_delta_3bar"].rolling(week, min_periods=one_day).mean()
        std = result["normalized_delta_3bar"].rolling(week, min_periods=one_day).std()
        result["delta_zscore"] = (result["normalized_delta_3bar"] - mean) / std.replace(0, np.nan)
        result["abs_return_percentile"] = _rolling_percentile(result["return_3bar"].abs(), week, one_day)
        result["price_cvd_divergence"] = np.select(
            [
                (result["return_12bar"] > 0) & (result["delta_12bar"] < 0),
                (result["return_12bar"] < 0) & (result["delta_12bar"] > 0),
            ], ["PRICE_UP_CVD_DOWN", "PRICE_DOWN_CVD_UP"], default="NONE",
        )
        result["absorption_ratio"] = result["normalized_delta_3bar"].abs() / (
            result["return_3bar"].abs() / (result["atr14"] / close).replace(0, np.nan)
        ).replace(0, np.nan)
        if "oi_usd" in result:
            oi = pd.to_numeric(result["oi_usd"])
            result["oi_change_3bar"] = oi.pct_change(3, fill_method=None)
            result["oi_change_12bar"] = oi.pct_change(12, fill_method=None)
            result["oi_change_percentile"] = _rolling_percentile(result["oi_change_3bar"], week, one_day)
        if "funding_rate" in result:
            funding = pd.to_numeric(result["funding_rate"])
            result["funding_percentile"] = _rolling_percentile(funding, one_day * 30, one_day * 7)
        return result


class FlowShadowGate:
    @staticmethod
    def evaluate(data_status: str, *, required_age_seconds: float | None = None, maximum_age_seconds: float = 360.0) -> dict[str, Any]:
        status = str(data_status).upper()
        valid = status == FlowIntegrityStatus.VALID.value
        fresh = required_age_seconds is not None and 0 <= required_age_seconds <= maximum_age_seconds
        return {
            "decision": "OBSERVE_ONLY" if valid and fresh else "NO_SIGNAL",
            "reason": "FLOW_DATA_VALID" if valid and fresh else "DATA_INVALID",
            "execution_authority": False,
        }
