from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from services.analyzer import Analyzer
from services.cache import cache
from services.data_integrity import DataIntegrityEngine
from services.decision_quality import DecisionQualityEngine
from services.market import Market
from utils.timeframe import timeframe_seconds


REFERENCE = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)


def _frame(timeframe: str = "1m", periods: int = 230, *, provider: bool = True) -> pd.DataFrame:
    seconds = timeframe_seconds(timeframe)
    assert seconds
    times = pd.date_range(
        end=pd.Timestamp(REFERENCE) - pd.Timedelta(seconds=seconds),
        periods=periods,
        freq=pd.Timedelta(seconds=seconds),
        tz="UTC",
    )
    values = np.arange(periods, dtype=float) + 100.0
    frame = pd.DataFrame({
        "time": times,
        "open": values,
        "high": values + 2.0,
        "low": values - 1.0,
        "close": values + 1.0,
        "volume": np.full(periods, 10.0),
        "confirm": ["1"] * periods,
    })
    if provider:
        frame.attrs.update({
            "market_data_provider": "FIXTURE",
            "market_data_symbol": "BTC",
            "market_data_timeframe": timeframe,
            "market_data_requested_limit": periods,
            "market_data_closed_semantics": "INCLUDES_FORMING",
            "market_data_fallback_used": False,
        })
    return frame


def _validate(frame: pd.DataFrame, timeframe: str = "1m", *, minimum: int = 220, reference=REFERENCE):
    return DataIntegrityEngine().prepare_market_frame(
        frame, timeframe=timeframe, minimum_history=minimum,
        reference_time=reference, require_freshness=True,
    )


@pytest.mark.parametrize("timeframe", ["1m", "4h"])
def test_regular_closed_candles_are_valid_across_timeframes(timeframe):
    result = _validate(_frame(timeframe), timeframe)
    assert result.valid and result.status == "VALID"
    assert result.quality()["closed_candle_semantics"] == "CLOSED_ONLY"


def test_reverse_provider_order_is_normalized_chronologically():
    result = _validate(_frame().iloc[::-1].copy())
    assert result.valid
    assert result.quality()["ordering"] == "OUT_OF_ORDER_NORMALIZED"
    assert result.frame["time"].is_monotonic_increasing


def test_forming_candle_is_excluded_not_used_as_closed_history():
    frame = _frame()
    forming = frame.iloc[[-1]].copy()
    forming["time"] = pd.Timestamp(REFERENCE)
    forming["confirm"] = "0"
    frame = pd.concat([frame, forming], ignore_index=True)
    frame.attrs.update(_frame().attrs)
    result = _validate(frame)
    assert result.valid
    assert result.quality()["forming_candles_removed"] == 1
    assert result.frame["time"].max() < pd.Timestamp(REFERENCE)


def test_freshness_is_timeframe_based_and_future_candles_fail_closed():
    minute = _validate(_frame("1m"), "1m")
    stale = _validate(_frame("1m"), "1m", reference=REFERENCE + timedelta(minutes=3))
    four_hour = _validate(_frame("4h"), "4h", reference=REFERENCE + timedelta(hours=1))
    future = _frame()
    future.loc[future.index[-1], "time"] = pd.Timestamp(REFERENCE) + pd.Timedelta(minutes=1)
    future_result = _validate(future)
    assert minute.valid
    assert not stale.valid and stale.status == "STALE"
    assert four_hour.valid
    assert not future_result.valid and future_result.code == "FUTURE_CANDLE"


def test_gaps_and_duplicate_conflicts_are_explicit():
    one_gap = _frame().drop(index=100).reset_index(drop=True)
    multi_gap = _frame().drop(index=[100, 101, 102]).reset_index(drop=True)
    exact_duplicate = pd.concat([_frame(), _frame().iloc[[100]]], ignore_index=True)
    exact_duplicate.attrs.update(_frame().attrs)
    conflict = exact_duplicate.copy()
    conflict.loc[conflict.index[-1], "close"] += 5
    conflict.loc[conflict.index[-1], "high"] += 5
    conflict.attrs.update(_frame().attrs)
    one = _validate(one_gap)
    multiple = _validate(multi_gap)
    duplicate = _validate(exact_duplicate)
    conflicting = _validate(conflict)
    assert one.status == "GAPPED" and one.quality()["missing_candle_count"] == 1
    assert multiple.status == "GAPPED" and multiple.quality()["missing_candle_count"] == 3
    assert duplicate.valid and duplicate.quality()["exact_duplicates_removed"] == 1
    assert not conflicting.valid and conflicting.status == "CONFLICTING"


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda frame: frame.assign(high=frame["low"] - 1), "INVALID_OHLC"),
        (lambda frame: frame.assign(open=-1.0), "NEGATIVE_PRICE"),
        (lambda frame: frame.assign(volume=-1.0), "NEGATIVE_VOLUME"),
        (lambda frame: frame.assign(close=np.nan), "NON_FINITE_OHLCV"),
        (lambda frame: frame.assign(close=np.inf), "NON_FINITE_OHLCV"),
        (lambda frame: frame.assign(time="not-a-timestamp"), "MALFORMED_TIMESTAMP"),
    ],
)
def test_structural_invalidity_fails_closed(mutate, code):
    frame = mutate(_frame())
    frame.attrs.update(_frame().attrs)
    result = _validate(frame)
    assert not result.valid and result.code == code


def test_insufficient_history_and_unknown_timeframe_are_not_valid():
    insufficient = _validate(_frame(periods=20), minimum=220)
    unknown = DataIntegrityEngine().prepare_market_frame(_frame(), timeframe="banana", require_freshness=True)
    market_unknown = asyncio.run(Market().get_klines("BTC", "banana", 230))
    unknown_analysis = Analyzer().analyze(market_unknown, symbol="BTC", timeframe="banana", use_cache=False)
    assert insufficient.status == "INSUFFICIENT"
    assert unknown.status == "UNKNOWN"
    assert market_unknown.attrs["market_data_quality"]["status"] == "UNKNOWN"
    assert unknown_analysis["direction"] == "NO_TRADE"


class _FailingProvider:
    async def get_klines(self, **_kwargs):
        raise TimeoutError("fixture timeout")


class _MalformedProvider:
    async def get_klines(self, **_kwargs):
        return pd.DataFrame({"time": [REFERENCE], "close": [100.0]})


class _NonFrameProvider:
    async def get_klines(self, **_kwargs):
        return {"data": "malformed"}


def test_provider_failures_are_explicit_not_empty_valid_data():
    failed = Market()
    failed.provider = _FailingProvider()
    malformed = Market()
    malformed.provider = _MalformedProvider()
    non_frame = Market()
    non_frame.provider = _NonFrameProvider()
    failed_frame = asyncio.run(failed.get_klines("BTC", "1m", 230))
    malformed_frame = asyncio.run(malformed.get_klines("ETH", "1m", 230))
    non_frame_result = asyncio.run(non_frame.get_klines("SOL", "1m", 230))
    assert failed_frame.attrs["market_data_quality"]["status"] == "PROVIDER_ERROR"
    assert malformed_frame.attrs["market_data_quality"]["status"] == "INCOMPLETE"
    assert non_frame_result.attrs["market_data_quality"]["status"] == "PROVIDER_ERROR"


class _NeverCalledProvider:
    async def get_klines(self, **_kwargs):  # pragma: no cover - cache assertion protects this branch
        raise AssertionError("stale fixture should be served from cache")


def test_cache_retrieval_does_not_reset_candle_freshness():
    market = Market()
    market.provider = _NeverCalledProvider()
    stale = _frame()
    stale["time"] = stale["time"] - pd.Timedelta(days=2)
    stale.attrs.update({
        "market_data_provider": "FIXTURE",
        "market_data_symbol": "CACHESTALE",
        "market_data_timeframe": "1m",
        "market_data_requested_limit": 230,
    })
    key = f"{type(market.provider).__name__}:CACHESTALE:1m:230"
    cache.set(key, stale, ttl=20)
    result = asyncio.run(market.get_klines("CACHESTALE", "1m", 230))
    assert result.attrs["market_data_cache_hit"] is True
    assert result.attrs["market_data_quality"]["status"] == "STALE"


def test_invalid_runtime_candles_produce_no_trade_for_all_phase1b_sources():
    stale = _frame()
    stale["time"] = stale["time"] - pd.Timedelta(days=1)
    stale.attrs.update(_frame().attrs)
    analysis = Analyzer().analyze(stale, symbol="BTC", timeframe="1m", source="scanner", use_cache=False)
    assert analysis["direction"] == "NO_TRADE"
    assert analysis["data_quality"]["status"] == "STALE"
    outcomes = []
    for source in ("MANUAL_ANALYZE", "SCANNER", "WATCH_ENGINE", "OBSERVATION_MONITOR"):
        decision = DecisionQualityEngine().enrich(dict(analysis), source=source)
        outcomes.append((decision["decision_outcome"], decision["decision_data_quality"]))
    assert outcomes == [("NO_TRADE", "STALE")] * 4
