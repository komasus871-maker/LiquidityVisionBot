from __future__ import annotations

from dataclasses import asdict

import numpy as np
import pandas as pd

from services.analyzer import Analyzer
from services.research_features import (
    ATR, BOS, DISPLACEMENT, EMA50, EMA200, FVG_LABEL, MACD_LINE, MACD_SIGNAL,
    PREMIUM, REGIME, RSI, STRUCTURE, SWEEP, VOLUME, attach_causal_feature_cache,
)
from services.research_replay import EntryPolicy, HistoricalReplayEngine, ReplayConfig, ReplayCostModel, _as_utc
from utils.breaker_block import BreakerBlock
from utils.fvg import FVG
from utils.mitigation_block import MitigationBlock
from utils.order_blocks import OrderBlocks
from utils.structure import Structure
from utils.timeframe import timeframe_seconds


def _frame(count: int = 280) -> pd.DataFrame:
    rng = np.random.default_rng(20260919)
    close = 100 + np.cumsum(rng.normal(0, 1.4, count))
    opened = np.r_[close[0], close[:-1]] + rng.normal(0, .35, count)
    high = np.maximum(opened, close) + rng.uniform(.05, 1.2, count)
    low = np.minimum(opened, close) - rng.uniform(.05, 1.2, count)
    return pd.DataFrame({
        "time": pd.date_range("2024-01-01", periods=count, freq="h", tz="UTC"),
        "open": opened,
        "high": high,
        "low": low,
        "close": close,
        "volume": rng.uniform(100, 1000, count),
    })


def _legacy_fvg(frame: pd.DataFrame) -> tuple[list[dict], list[dict], str]:
    candles = frame.reset_index(drop=True)
    bullish, bearish = [], []
    for index in range(2, len(candles)):
        first, third = candles.iloc[index - 2], candles.iloc[index]
        if first["high"] < third["low"]:
            bullish.append({"type": "bullish", "low": float(first["high"]),
                            "high": float(third["low"]), "index": index})
        if first["low"] > third["high"]:
            bearish.append({"type": "bearish", "low": float(third["high"]),
                            "high": float(first["low"]), "index": index})
    price = float(candles.iloc[-1]["close"])
    active = next((gap for gap in reversed(bullish) if gap["low"] <= price <= gap["high"]), None)
    if active is None:
        active = next((gap for gap in reversed(bearish) if gap["low"] <= price <= gap["high"]), None)
    if active:
        label = "Bullish Active FVG" if active["type"] == "bullish" else "Bearish Active FVG"
        icon = "🟢" if active["type"] == "bullish" else "🔴"
        return bullish, bearish, f"{icon} {label} ({active['low']:.2f} - {active['high']:.2f})"
    gaps = bullish + bearish
    if not gaps:
        return bullish, bearish, "⚪ No Fair Value Gap"
    nearest = min(gaps, key=lambda gap: min(abs(price - gap["low"]), abs(price - gap["high"])))
    label = "Bullish FVG" if nearest["type"] == "bullish" else "Bearish FVG"
    icon = "🟢" if nearest["type"] == "bullish" else "🔴"
    return bullish, bearish, f"{icon} {label} ({nearest['low']:.2f} - {nearest['high']:.2f})"


def _legacy_breaker(frame: pd.DataFrame, bullish: bool) -> dict | None:
    candles = frame.tail(50).reset_index(drop=True)
    for index in range(5, len(candles) - 2):
        candle = candles.iloc[index]
        if bullish and candle["close"] >= candle["open"]:
            continue
        if not bullish and candle["close"] <= candle["open"]:
            continue
        broken = any(
            candles.iloc[later]["close"] > candle["high"] if bullish
            else candles.iloc[later]["close"] < candle["low"]
            for later in range(index + 1, len(candles))
        )
        price = float(candles.iloc[-1]["close"])
        if broken and candle["low"] <= price <= candle["high"]:
            return {"type": "bullish" if bullish else "bearish",
                    "low": float(candle["low"]), "high": float(candle["high"])}
    return None


def _legacy_order_block(frame: pd.DataFrame, bullish: bool) -> dict | None:
    candles = frame.tail(40).reset_index(drop=True)
    for index in range(len(candles) - 3, 2, -1):
        candle, following = candles.iloc[index], candles.iloc[index + 1]
        matches = (
            candle["close"] < candle["open"] and following["close"] > candle["high"]
            if bullish else
            candle["close"] > candle["open"] and following["close"] < candle["low"]
        )
        if matches:
            return {"type": "bullish" if bullish else "bearish",
                    "high": float(candle["high"]), "low": float(candle["low"]), "index": index}
    return None


def _legacy_mitigation(frame: pd.DataFrame, bullish: bool) -> dict | None:
    candles = frame.tail(50).reset_index(drop=True)
    price = float(candles.iloc[-1]["close"])
    for index in range(len(candles) - 3):
        candle, impulse = candles.iloc[index], candles.iloc[index + 1]
        matches = (
            candle["close"] < candle["open"] and impulse["close"] > candle["high"]
            if bullish else
            candle["close"] > candle["open"] and impulse["close"] < candle["low"]
        )
        if matches and candle["low"] <= price <= candle["high"]:
            return {"type": "bullish" if bullish else "bearish",
                    "low": float(candle["low"]), "high": float(candle["high"])}
    return None


def _legacy_swings(frame: pd.DataFrame, column: str, high: bool) -> list[tuple[int, float]]:
    values = frame[column].values
    result = []
    for index in range(2, len(values) - 2):
        neighbors = (values[index - 1], values[index - 2], values[index + 1], values[index + 2])
        if all(values[index] > value for value in neighbors) if high else all(values[index] < value for value in neighbors):
            result.append((index, values[index]))
    return result


def test_vectorized_pattern_detectors_have_exact_legacy_prefix_parity():
    frame = _frame()
    for length in (*range(3, 15), *range(20, len(frame) + 1, 7)):
        prefix = frame.iloc[:length]
        old_bull, old_bear, old_label = _legacy_fvg(prefix)
        fvg = FVG(prefix)
        assert fvg.bullish() == old_bull
        assert fvg.bearish() == old_bear
        assert fvg.analyze() == old_label

        breaker = BreakerBlock(prefix)
        assert breaker.bullish() == _legacy_breaker(prefix, True)
        assert breaker.bearish() == _legacy_breaker(prefix, False)

        order = OrderBlocks(prefix)
        assert order.bullish() == _legacy_order_block(prefix, True)
        assert order.bearish() == _legacy_order_block(prefix, False)

        mitigation = MitigationBlock(prefix)
        assert mitigation.bullish() == _legacy_mitigation(prefix, True)
        assert mitigation.bearish() == _legacy_mitigation(prefix, False)

        structure = Structure(prefix)
        assert structure.swing_highs() == _legacy_swings(prefix, "high", True)
        assert structure.swing_lows() == _legacy_swings(prefix, "low", False)


def test_sparse_plan_replay_is_exactly_equal_to_canonical_factory_replay():
    frame = _frame(245)
    config = ReplayConfig(
        timeframe="1h",
        entry_policy=EntryPolicy.MARKET_NEXT_OPEN,
        costs=ReplayCostModel(fee_rate=.0007, market_slippage_rate=.0003),
    )
    engine = HistoricalReplayEngine(config)
    seconds = timeframe_seconds(config.timeframe)
    assert seconds
    plans = {}
    for index in (219, 226, 237):
        entry = float(frame.iloc[index]["close"])
        decision_at = _as_utc(frame.iloc[index]["time"] + pd.Timedelta(seconds=seconds)).isoformat()
        plans[decision_at] = {
            "signal_id": f"signal-{index}", "strategy_version": "parity-v1", "symbol": "BTC",
            "direction": "LONG", "entry": entry, "stop": entry * .98, "tp1": entry * 1.04,
            "entry_policy": EntryPolicy.MARKET_NEXT_OPEN.value,
            "metadata": {"decision_at": decision_at},
        }

    def factory(snapshot: pd.DataFrame):
        decision_at = _as_utc(snapshot.iloc[-1]["time"] + pd.Timedelta(seconds=seconds)).isoformat()
        return plans.get(decision_at)

    metadata = {"data_provider": "FIXTURE", "decision_source": "PARITY",
                "decision_authority": "DecisionQualityEngine", "decision_version": "v1"}
    canonical = engine.run(frame, factory, metadata=metadata)
    accelerated = engine.run_plans(frame, plans, metadata=metadata)
    assert [asdict(item) for item in accelerated] == [asdict(item) for item in canonical]


def test_prevalidated_research_pipeline_preserves_all_economic_analysis_fields():
    frame = _frame(245)
    prepared = HistoricalReplayEngine(ReplayConfig(timeframe="1h")).prepare(frame)
    prefix = prepared.iloc[:238]
    canonical = Analyzer().analyze(prefix, symbol="BTC", timeframe="1h", source="PARITY",
                                   use_cache=False, research_envelope=False)
    accelerated = Analyzer().analyze(prefix, symbol="BTC", timeframe="1h", source="PARITY",
                                     use_cache=False, research_envelope=False, prevalidated_research=True)
    fields = (
        "price", "ema50", "ema200", "trend", "structure", "bos", "choch", "liquidity", "sweep",
        "order_block", "breaker", "mitigation", "fvg", "premium", "volume", "rsi", "macd",
        "macd_bullish", "displacement", "atr", "market_regime", "direction", "long_score",
        "short_score", "directional_edge", "entry_quality", "risk_quality", "execution_readiness",
        "entry", "stop", "tp1", "tp2", "tp3", "rr", "score_components", "plan_valid",
    )
    assert {key: accelerated.get(key) for key in fields} == {key: canonical.get(key) for key in fields}


def test_causal_feature_stream_matches_canonical_prefixes_and_ignores_future_rows():
    frame = _frame(260)
    prepared = HistoricalReplayEngine(ReplayConfig(timeframe="1h")).prepare(frame)
    cached = attach_causal_feature_cache(prepared.copy())
    columns = (EMA50, EMA200, STRUCTURE, BOS, SWEEP, FVG_LABEL, PREMIUM, VOLUME,
               RSI, MACD_LINE, MACD_SIGNAL, DISPLACEMENT, ATR, REGIME)
    economic = (
        "price", "ema50", "ema200", "trend", "structure", "bos", "choch", "liquidity", "sweep",
        "order_block", "breaker", "mitigation", "fvg", "premium", "volume", "rsi", "macd",
        "macd_bullish", "displacement", "atr", "market_regime", "direction", "long_score",
        "short_score", "directional_edge", "entry_quality", "risk_quality", "execution_readiness",
        "entry", "stop", "tp1", "tp2", "tp3", "rr", "score_components", "plan_valid",
    )
    for index in (219, 226, 244, 258):
        truncated = attach_causal_feature_cache(prepared.iloc[:index + 1].copy())
        assert {column: cached.iloc[index][column] for column in columns} == {
            column: truncated.iloc[-1][column] for column in columns
        }
        canonical = Analyzer().analyze(prepared.iloc[:index + 1], symbol="BTC", timeframe="1h",
                                       source="PARITY", use_cache=False, research_envelope=False)
        accelerated = Analyzer().analyze(cached.iloc[:index + 1], symbol="BTC", timeframe="1h",
                                         source="PARITY", use_cache=False, research_envelope=False,
                                         prevalidated_research=True)
        assert {key: accelerated.get(key) for key in economic} == {
            key: canonical.get(key) for key in economic
        }
