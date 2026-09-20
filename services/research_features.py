"""Causal, immutable feature streams for exact offline prefix replay.

The cache is attached only after a complete dataset has passed the canonical
historical candle contract.  Every value at row *i* uses rows ``<= i`` and is
constructed from the same formulas as the production analysis utilities.
Production market frames never receive the opt-in marker.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from services.market_regime import MarketRegimeEngine, RegimeResult
from utils.indicators import ema, macd, rsi


CACHE_VERSION = "causal-prefix-features-v1"
CACHE_ATTR = "_lv_research_causal_cache"
EMA50 = "__lv_ema50"
EMA200 = "__lv_ema200"
STRUCTURE = "__lv_structure"
BOS = "__lv_bos"
SWEEP = "__lv_sweep"
FVG_LABEL = "__lv_fvg"
PREMIUM = "__lv_premium"
VOLUME = "__lv_volume"
RSI = "__lv_rsi"
MACD_LINE = "__lv_macd_line"
MACD_SIGNAL = "__lv_macd_signal"
DISPLACEMENT = "__lv_displacement"
ATR = "__lv_atr"
REGIME = "__lv_market_regime"


def cache_enabled(frame: pd.DataFrame) -> bool:
    return (getattr(frame, "attrs", {}) or {}).get(CACHE_ATTR) == CACHE_VERSION


def _structure_stream(frame: pd.DataFrame) -> tuple[list[str], list[str]]:
    highs = frame["high"].to_numpy(dtype=float)
    lows = frame["low"].to_numpy(dtype=float)
    closes = frame["close"].to_numpy(dtype=float)
    if len(frame) < 5:
        return ["⚪ Unknown"] * len(frame), ["⚪ No BOS"] * len(frame)
    high_mask = ((highs[2:-2] > highs[1:-3]) & (highs[2:-2] > highs[:-4])
                 & (highs[2:-2] > highs[3:-1]) & (highs[2:-2] > highs[4:]))
    low_mask = ((lows[2:-2] < lows[1:-3]) & (lows[2:-2] < lows[:-4])
                & (lows[2:-2] < lows[3:-1]) & (lows[2:-2] < lows[4:]))
    high_indices = np.flatnonzero(high_mask) + 2
    low_indices = np.flatnonzero(low_mask) + 2
    structures: list[str] = []
    breakouts: list[str] = []
    for end in range(len(frame)):
        visible_highs = int(np.searchsorted(high_indices, end - 2, side="right"))
        visible_lows = int(np.searchsorted(low_indices, end - 2, side="right"))
        if visible_highs >= 2 and visible_lows >= 2:
            last_high, previous_high = highs[high_indices[visible_highs - 1]], highs[high_indices[visible_highs - 2]]
            last_low, previous_low = lows[low_indices[visible_lows - 1]], lows[low_indices[visible_lows - 2]]
            if last_high > previous_high and last_low > previous_low:
                structure = "🟢 Bullish"
            elif last_high < previous_high and last_low < previous_low:
                structure = "🔴 Bearish"
            else:
                structure = "🟡 Range"
        else:
            structure = "⚪ Unknown"
        if visible_highs and closes[end] > highs[high_indices[visible_highs - 1]]:
            bos = f"🟢 Bullish BOS ({highs[high_indices[visible_highs - 1]]:.2f})"
        elif visible_lows and closes[end] < lows[low_indices[visible_lows - 1]]:
            bos = f"🔴 Bearish BOS ({lows[low_indices[visible_lows - 1]]:.2f})"
        else:
            bos = "⚪ No BOS"
        structures.append(structure)
        breakouts.append(bos)
    return structures, breakouts


def _sweep_stream(frame: pd.DataFrame) -> list[str]:
    highs = frame["high"].to_numpy(dtype=float)
    lows = frame["low"].to_numpy(dtype=float)
    closes = frame["close"].to_numpy(dtype=float)
    result: list[str] = []
    prior_high = prior_low = None
    for index in range(len(frame)):
        if index:
            previous_high = float(highs[index - 1])
            previous_low = float(lows[index - 1])
            prior_high = previous_high if prior_high is None else max(prior_high, previous_high)
            prior_low = previous_low if prior_low is None else min(prior_low, previous_low)
        if index + 1 >= 5 and prior_high is not None and highs[index] > prior_high and closes[index] < prior_high:
            result.append("🔴 Buy Side Stop Hunt")
            continue
        if index + 1 >= 5 and prior_low is not None and lows[index] < prior_low and closes[index] > prior_low:
            result.append("🟢 Sell Side Stop Hunt")
            continue
        start = max(0, index - 19)
        equilibrium = (float(np.max(highs[start:index + 1])) + float(np.min(lows[start:index + 1]))) / 2
        result.append(f"⚪ Internal Liquidity ({'bullish' if closes[index] > equilibrium else 'bearish'})")
    return result


def _fvg_stream(frame: pd.DataFrame) -> list[str]:
    highs = frame["high"].to_numpy(dtype=float)
    lows = frame["low"].to_numpy(dtype=float)
    closes = frame["close"].to_numpy(dtype=float)
    if len(frame) < 3:
        return ["⚪ No Fair Value Gap"] * len(frame)
    bullish_indices = np.flatnonzero(highs[:-2] < lows[2:]) + 2
    bullish_low, bullish_high = highs[bullish_indices - 2], lows[bullish_indices]
    bearish_indices = np.flatnonzero(lows[:-2] > highs[2:]) + 2
    bearish_low, bearish_high = highs[bearish_indices], lows[bearish_indices - 2]
    result: list[str] = []
    for end, price in enumerate(closes):
        bull_count = int(np.searchsorted(bullish_indices, end, side="right"))
        bear_count = int(np.searchsorted(bearish_indices, end, side="right"))
        bull_active = np.flatnonzero((bullish_low[:bull_count] <= price) & (price <= bullish_high[:bull_count]))
        if bull_active.size:
            index = int(bull_active[-1])
            result.append(f"🟢 Bullish Active FVG ({bullish_low[index]:.2f} - {bullish_high[index]:.2f})")
            continue
        bear_active = np.flatnonzero((bearish_low[:bear_count] <= price) & (price <= bearish_high[:bear_count]))
        if bear_active.size:
            index = int(bear_active[-1])
            result.append(f"🔴 Bearish Active FVG ({bearish_low[index]:.2f} - {bearish_high[index]:.2f})")
            continue
        bull_distance = np.minimum(abs(price - bullish_low[:bull_count]), abs(price - bullish_high[:bull_count]))
        bear_distance = np.minimum(abs(price - bearish_low[:bear_count]), abs(price - bearish_high[:bear_count]))
        if not bull_count and not bear_count:
            result.append("⚪ No Fair Value Gap")
            continue
        combined = np.concatenate((bull_distance, bear_distance))
        nearest = int(np.argmin(combined))
        if nearest < bull_count:
            result.append(f"🟢 Bullish FVG ({bullish_low[nearest]:.2f} - {bullish_high[nearest]:.2f})")
        else:
            nearest -= bull_count
            result.append(f"🔴 Bearish FVG ({bearish_low[nearest]:.2f} - {bearish_high[nearest]:.2f})")
    return result


def _regime_stream(frame: pd.DataFrame) -> list[dict[str, Any]]:
    engine = MarketRegimeEngine()
    close = pd.to_numeric(frame["close"], errors="coerce").astype(float)
    closes = close.to_numpy(dtype=float)
    ema50 = close.ewm(span=50, adjust=False).mean().to_numpy(dtype=float)
    ema200 = close.ewm(span=200, adjust=False).mean().to_numpy(dtype=float)
    tr = engine._true_range(frame)
    atr = tr.rolling(14, min_periods=5).mean().to_numpy(dtype=float)
    atr_pct = (pd.Series(atr, index=frame.index) / close.abs().replace(0, np.nan)).to_numpy(dtype=float)
    returns = close.pct_change().to_numpy(dtype=float)
    result: list[dict[str, Any]] = []
    unknown = RegimeResult(
        code="UNKNOWN", label="⚪ Unknown / Insufficient Data", confidence=0.0,
        trend_strength=0.0, efficiency=0.0, volatility_percentile=50.0,
        volatility_state="NORMAL", direction="NEUTRAL", execution_mode="OBSERVE",
        allows_trend_entry=False, risk_multiplier=0.5,
        reasons=("Insufficient confirmed candles for regime classification",),
    ).as_dict()
    for index in range(len(frame)):
        if index < 79:
            result.append(dict(unknown))
            continue
        current_price = max(abs(float(closes[index])), 1e-12)
        current_atr = max(float(atr[index]), current_price * 1e-8)
        start = index - engine.LOOKBACK + 1
        window = closes[start:index + 1]
        path = float(np.abs(np.diff(window)).sum())
        net = float(abs(window[-1] - window[0]))
        efficiency = 0.0 if path <= 1e-12 else min(1.0, net / path)
        ema_spread_atr = abs(float(ema50[index] - ema200[index])) / current_atr
        ema_slope_atr = abs(float(ema50[index] - ema50[index - engine.SLOPE_LOOKBACK + 1])) / current_atr
        trend_strength_raw = ema_spread_atr * .45 + ema_slope_atr * .35 + efficiency * 3.0 * .20
        trend_strength = min(100.0, trend_strength_raw / 2.25 * 100.0)
        vol_window = atr_pct[max(0, index - 179):index + 1]
        clean = vol_window[np.isfinite(vol_window)]
        vol_percentile = float(np.mean(clean <= atr_pct[index]) * 100.0) if clean.size else 50.0
        if vol_percentile >= 85:
            volatility_state = "EXTREME"
        elif vol_percentile >= 68:
            volatility_state = "HIGH"
        elif vol_percentile <= 20:
            volatility_state = "COMPRESSED"
        else:
            volatility_state = "NORMAL"
        return_window = returns[max(0, index - engine.LOOKBACK + 1):index + 1]
        return_window = return_window[np.isfinite(return_window)]
        positive_share = float(np.mean(return_window > 0)) if return_window.size else .5
        direction_consistency = abs(positive_share - .5) * 2.0
        price_above = closes[index] > ema50[index] > ema200[index]
        price_below = closes[index] < ema50[index] < ema200[index]
        direction = "LONG" if price_above else "SHORT" if price_below else "NEUTRAL"
        reasons: list[str] = []
        if efficiency >= .48:
            reasons.append(f"Directional price-path efficiency is high ({efficiency * 100:.0f}%)")
        elif efficiency <= .25:
            reasons.append(f"Price path is choppy ({efficiency * 100:.0f}% efficiency)")
        if ema_spread_atr >= 1.0:
            reasons.append("EMA separation confirms directional expansion")
        elif ema_spread_atr <= .35:
            reasons.append("EMA compression suggests weak directional control")
        if volatility_state == "EXTREME":
            reasons.append("Volatility is at an extreme historical percentile")
        elif volatility_state == "COMPRESSED":
            reasons.append("Volatility is compressed and breakout risk is elevated")
        strong_trend = direction != "NEUTRAL" and efficiency >= .38 and ema_spread_atr >= .65 and ema_slope_atr >= .45
        choppy = efficiency <= .27 and ema_spread_atr <= .75
        compressed = volatility_state == "COMPRESSED" and ema_spread_atr <= .65
        extreme_expansion = volatility_state == "EXTREME" and (ema_slope_atr >= 1.25 or efficiency >= .58)
        if extreme_expansion and direction != "NEUTRAL":
            code, label, mode, allows, risk = "VOLATILE_EXPANSION", "🟠 Volatile Expansion", "WAIT FOR PULLBACK / REDUCE SIZE", False, .55
            confidence = min(96.0, 62 + vol_percentile * .25 + efficiency * 20)
        elif strong_trend:
            code = "TRENDING"
            label = f"{'🟢' if direction == 'LONG' else '🔴'} Trending {direction.title()}"
            mode, allows = "TREND FOLLOWING", volatility_state != "EXTREME"
            risk = 1.0 if volatility_state == "NORMAL" else .8
            confidence = min(96.0, 45 + trend_strength * .35 + efficiency * 35 + direction_consistency * 12)
        elif compressed:
            code, label, mode, allows, risk = "COMPRESSION", "🟣 Volatility Compression", "WAIT FOR CONFIRMED BREAKOUT", False, .5
            confidence = min(92.0, 58 + (20 - vol_percentile) * .8 + (.65 - ema_spread_atr) * 20)
        elif choppy:
            code, label, mode, allows, risk = "RANGING", "🟡 Ranging / Choppy", "AVOID TREND ENTRIES", False, .45
            confidence = min(94.0, 58 + (.27 - efficiency) * 90 + (.75 - ema_spread_atr) * 15)
        else:
            code, label, mode, allows, risk = "TRANSITION", "⚪ Transitional / Mixed", "REQUIRE EXTRA CONFIRMATION", False, .65
            confidence = min(88.0, 52 + abs(trend_strength - 50) * .25 + direction_consistency * 10)
        if not reasons:
            reasons.append("Trend and volatility evidence are mixed")
        result.append(RegimeResult(
            code=code, label=label, confidence=max(0.0, confidence), trend_strength=trend_strength,
            efficiency=efficiency * 100.0, volatility_percentile=vol_percentile,
            volatility_state=volatility_state, direction=direction, execution_mode=mode,
            allows_trend_entry=allows, risk_multiplier=risk, reasons=tuple(reasons[:4]),
        ).as_dict())
    return result


def attach_causal_feature_cache(frame: pd.DataFrame) -> pd.DataFrame:
    """Attach exact causal values to an already validated mutable replay frame."""
    if cache_enabled(frame):
        return frame
    close = pd.to_numeric(frame["close"], errors="coerce").astype(float)
    frame[EMA50] = ema(frame, 50)
    frame[EMA200] = ema(frame, 200)
    frame[RSI] = rsi(frame)
    line, signal = macd(frame)
    frame[MACD_LINE], frame[MACD_SIGNAL] = line, signal

    high = pd.to_numeric(frame["high"], errors="coerce").astype(float)
    low = pd.to_numeric(frame["low"], errors="coerce").astype(float)
    opened = pd.to_numeric(frame["open"], errors="coerce").astype(float)
    volume = pd.to_numeric(frame["volume"], errors="coerce").astype(float)
    true_range = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = true_range.rolling(14).mean()
    atr_values = []
    for price, value in zip(close.to_numpy(dtype=float), atr.to_numpy(dtype=float)):
        value = 0.0 if pd.isna(value) else float(value)
        risk_distance = max(value * 1.5, float(price) * .0035)
        reward_unit = max(value * 2.0, risk_distance * 1.20)
        atr_values.append({
            "atr": value, "risk_distance": risk_distance,
            "long_stop": float(price) - risk_distance, "short_stop": float(price) + risk_distance,
            "long_tp": (float(price) + reward_unit, float(price) + reward_unit * 2, float(price) + reward_unit * 3),
            "short_tp": (float(price) - reward_unit, float(price) - reward_unit * 2, float(price) - reward_unit * 3),
        })
    frame[ATR] = pd.Series(atr_values, index=frame.index, dtype=object)

    bodies = (close - opened).abs()
    ranges = (high - low).clip(lower=0)
    average_body = bodies.rolling(20, min_periods=1).mean()
    displacement = []
    for op, cl, body, candle_range, average in zip(opened, close, bodies, ranges, average_body):
        direction = "Bullish" if cl > op else "Bearish" if cl < op else "Neutral"
        efficiency = round(float(body / candle_range * 100), 2) if candle_range else 0.0
        expansion = round(float(body / average), 2) if average else 0.0
        composite = min(100.0, efficiency * .65 + min(expansion / 2.0, 1.0) * 100 * .35)
        if direction == "Neutral":
            displacement.append("⚪ Weak Displacement (0.0%, 0.0x)")
        elif composite >= 72 and expansion >= 1.35:
            displacement.append(f"{'🟢' if direction == 'Bullish' else '🔴'} Strong {direction} Displacement ({efficiency}%, {expansion}x)")
        elif composite >= 48 and expansion >= .9:
            displacement.append(f"🟡 Moderate {direction} Displacement ({efficiency}%, {expansion}x)")
        else:
            displacement.append(f"⚪ Weak {direction} Displacement ({efficiency}%, {expansion}x)")
    frame[DISPLACEMENT] = displacement

    average_volume = volume.shift(1).rolling(20, min_periods=1).mean()
    volume_labels = []
    for op, hi, lo, cl, vol, average in zip(opened, high, low, close, volume, average_volume):
        ratio = round(float(vol / average), 2) if pd.notna(average) and average > 0 else 1.0
        efficiency = abs(float(cl - op)) / max(float(hi - lo), 0.0) if hi != lo else 0.0
        if ratio >= 2.0 and efficiency < .35:
            label = f"🔴 Volume Climax ({ratio}x)"
        elif ratio >= 2.0:
            label = f"🟢 Volume Spike ({ratio}x)"
        elif ratio >= 1.25:
            label = f"🟢 Elevated Volume ({ratio}x)"
        elif ratio < .55:
            label = f"⚪ Low Volume ({ratio}x)"
        else:
            label = f"⚪ Normal Volume ({ratio}x)"
        volume_labels.append(label)
    frame[VOLUME] = volume_labels

    rolling_high, rolling_low = high.rolling(80, min_periods=1).max(), low.rolling(80, min_periods=1).min()
    premium_values = []
    for price, maximum, minimum in zip(close, rolling_high, rolling_low):
        size = float(maximum - minimum)
        percent = 50.0 if size <= 0 else round(max(0.0, min(100.0, ((float(price) - minimum) / size) * 100)), 2)
        zone = "🔴 Premium" if percent >= 62 else "🟢 Discount" if percent <= 38 else "🟡 Equilibrium"
        premium_values.append({"zone": zone, "equilibrium": round(float(maximum + minimum) / 2, 2),
                               "premium": percent, "high": round(float(maximum), 2), "low": round(float(minimum), 2)})
    frame[PREMIUM] = pd.Series(premium_values, index=frame.index, dtype=object)
    structures, breakouts = _structure_stream(frame)
    frame[STRUCTURE], frame[BOS] = structures, breakouts
    frame[SWEEP] = _sweep_stream(frame)
    frame[FVG_LABEL] = _fvg_stream(frame)
    frame[REGIME] = pd.Series(_regime_stream(frame), index=frame.index, dtype=object)
    frame.attrs[CACHE_ATTR] = CACHE_VERSION
    return frame
