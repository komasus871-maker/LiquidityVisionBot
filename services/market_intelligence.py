from __future__ import annotations

import hashlib
import json
import math
import os
import statistics
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd


INTELLIGENCE_VERSION = "market-intelligence-v5"
STORY_VERSION = "market-story-v2"
LEVEL_VERSION = "level-intelligence-v1"
MICROSTRUCTURE_VERSION = "microstructure-v2"
QUALITY_VERSION = "signal-quality-v4"
RANK_VERSION = "signal-ranking-v5"

TIMEFRAME_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900,
    "1h": 3600, "4h": 14400, "1d": 86400,
}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError, OverflowError):
        return default


def _clip(value: Any, low: float = 0.0, high: float = 100.0) -> float:
    return round(max(low, min(high, _number(value))), 3)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False, default=str)


def _checksum(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(child) for child in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.isoformat()
    return value


def contains_raw_order_book(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).strip().lower() in {
                "bids", "asks", "order_book", "order_books", "raw_book", "raw_books", "snapshots",
            }:
                return True
            if contains_raw_order_book(child):
                return True
    elif isinstance(value, list):
        return any(contains_raw_order_book(child) for child in value)
    return False


def _timestamp_series(frame: pd.DataFrame) -> pd.Series | None:
    for key in ("timestamp", "datetime", "time", "open_time", "ts"):
        if key not in frame.columns:
            continue
        raw = frame[key]
        if pd.api.types.is_numeric_dtype(raw):
            maximum = _number(raw.dropna().max()) if not raw.dropna().empty else 0
            unit = "ms" if maximum > 10_000_000_000 else "s"
            parsed = pd.to_datetime(raw, unit=unit, utc=True, errors="coerce")
        else:
            parsed = pd.to_datetime(raw, utc=True, errors="coerce")
        if parsed.notna().any():
            return parsed
    if isinstance(frame.index, pd.DatetimeIndex):
        return pd.Series(pd.to_datetime(frame.index, utc=True), index=frame.index)
    return None


def _prepare_frame(dataframe: Any, timeframe: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    if dataframe is None or not hasattr(dataframe, "columns"):
        return pd.DataFrame(), {
            "status": "INVALID", "reason_codes": ["DATAFRAME_MISSING"],
            "usable_candles": 0, "timeframe": timeframe,
        }
    frame = dataframe.copy()
    source_count = len(frame)
    removed_unclosed = 0
    if "confirm" in frame.columns:
        confirmed = frame["confirm"].astype(str).str.lower().isin({"1", "true", "yes"})
        removed_unclosed = int((~confirmed).sum())
        frame = frame[confirmed].copy()
    required = ("open", "high", "low", "close")
    if any(column not in frame.columns for column in required):
        return pd.DataFrame(), {
            "status": "INVALID", "reason_codes": ["OHLC_COLUMNS_MISSING"],
            "usable_candles": 0, "source_candles": source_count,
            "unclosed_candles_removed": removed_unclosed, "timeframe": timeframe,
        }
    for column in (*required, "volume"):
        if column not in frame.columns:
            frame[column] = 0.0
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.replace([np.inf, -np.inf], np.nan).dropna(subset=list(required)).tail(600).reset_index(drop=True)
    bad_geometry = int(((frame["high"] < frame[["open", "close"]].max(axis=1)) |
                        (frame["low"] > frame[["open", "close"]].min(axis=1)) |
                        (frame["high"] < frame["low"])).sum()) if not frame.empty else 0
    reasons: list[str] = []
    if len(frame) < 60:
        reasons.append("INSUFFICIENT_HISTORY")
    if bad_geometry:
        reasons.append("INVALID_CANDLE_GEOMETRY")
    if removed_unclosed:
        reasons.append("UNCLOSED_CANDLES_EXCLUDED")
    timestamps = _timestamp_series(frame)
    last_timestamp = None
    if timestamps is not None and timestamps.notna().any():
        last_timestamp = timestamps.dropna().iloc[-1].isoformat()
    status = "GOOD" if len(frame) >= 80 and not bad_geometry else "DEGRADED" if len(frame) >= 30 else "INVALID"
    return frame, {
        "status": status, "reason_codes": reasons, "source_candles": source_count,
        "usable_candles": len(frame), "unclosed_candles_removed": removed_unclosed,
        "invalid_geometry_count": bad_geometry, "last_candle_at": last_timestamp,
        "timeframe": timeframe,
    }


def _true_range(frame: pd.DataFrame) -> pd.Series:
    previous = frame["close"].shift(1)
    return pd.concat(((frame["high"] - frame["low"]).abs(),
                      (frame["high"] - previous).abs(),
                      (frame["low"] - previous).abs()), axis=1).max(axis=1)


def _atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    return _true_range(frame).rolling(period, min_periods=max(3, period // 3)).mean()


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gains = delta.clip(lower=0).rolling(period, min_periods=period // 2).mean()
    losses = (-delta.clip(upper=0)).rolling(period, min_periods=period // 2).mean()
    rs = gains / losses.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50.0)


def _swing_points(frame: pd.DataFrame, width: int = 2) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for index in range(width, max(width, len(frame) - width)):
        window = frame.iloc[index - width:index + width + 1]
        high = float(frame.iloc[index]["high"])
        low = float(frame.iloc[index]["low"])
        if high >= float(window["high"].max()) and int((window["high"] == high).sum()) == 1:
            points.append({"kind": "SWING_HIGH", "price": high, "index": index})
        if low <= float(window["low"].min()) and int((window["low"] == low).sum()) == 1:
            points.append({"kind": "SWING_LOW", "price": low, "index": index})
    return points


class LevelIntelligenceEngine:
    """Deterministic price-area discovery with family-aware clustering."""

    FAMILY = {
        "SWING_HIGH": "STRUCTURE", "SWING_LOW": "STRUCTURE",
        "PREVIOUS_DAY_HIGH": "REFERENCE", "PREVIOUS_DAY_LOW": "REFERENCE",
        "SESSION_HIGH": "SESSION", "SESSION_LOW": "SESSION",
        "EQUAL_HIGHS": "LIQUIDITY", "EQUAL_LOWS": "LIQUIDITY",
        "ROUND_LEVEL": "PSYCHOLOGICAL", "FVG_LOW": "FVG", "FVG_HIGH": "FVG",
    }

    @staticmethod
    def _level(frame: pd.DataFrame, *, kind: str, price: float, index: int,
               timeframe: str, atr_value: float, current: float) -> dict[str, Any]:
        tolerance = max(abs(price) * .0005, atr_value * .12, 1e-12)
        later = frame.iloc[max(0, index):]
        touched = (later["low"] <= price + tolerance) & (later["high"] >= price - tolerance)
        touches = int(touched.sum())
        is_high = "HIGH" in kind
        failures = int((later["close"] > price + tolerance).sum()) if is_high else int((later["close"] < price - tolerance).sum())
        reactions = 0
        touch_indices = list(later.index[touched])[-6:]
        for touch_index in touch_indices:
            next_rows = frame.iloc[touch_index + 1:touch_index + 4]
            if next_rows.empty:
                continue
            move = (price - float(next_rows["low"].min())) if is_high else (float(next_rows["high"].max()) - price)
            reactions += int(move >= atr_value * .35)
        last_rows = frame.tail(8)
        swept = bool(((last_rows["high"] > price + tolerance) & (last_rows["close"] < price)).any()) if is_high else bool(
            ((last_rows["low"] < price - tolerance) & (last_rows["close"] > price)).any())
        age = max(0, len(frame) - 1 - index)
        freshness = max(0.0, 100 - touches * 12 - failures * 20 - age * .25)
        reaction_rate = reactions / max(1, touches)
        quality = _clip(35 + min(touches, 4) * 7 + reaction_rate * 25 - failures * 18 - int(swept) * 10)
        return {
            "type": kind, "family": LevelIntelligenceEngine.FAMILY.get(kind, "OTHER"),
            "price": round(price, 12), "timeframe": timeframe, "age_candles": age,
            "touches": touches, "successful_reactions": reactions, "failures": failures,
            "distance_pct": round(abs(current - price) / max(abs(current), 1e-12) * 100, 4),
            "liquidity_significance": quality, "freshness": _clip(freshness),
            "fresh": touches <= 1 and failures == 0, "swept": swept,
            "repeatedly_tested": touches >= 3, "source_index": index,
        }

    def analyze(self, frame: pd.DataFrame, timeframe: str) -> dict[str, Any]:
        if frame.empty:
            return {"version": LEVEL_VERSION, "levels": [], "clusters": [], "status": "UNAVAILABLE"}
        current = float(frame.iloc[-1]["close"])
        atr_value = max(_number(_atr(frame).iloc[-1]), abs(current) * .001)
        levels: list[dict[str, Any]] = []
        points = _swing_points(frame)
        for point in points[-40:]:
            levels.append(self._level(frame, kind=point["kind"], price=point["price"],
                                      index=point["index"], timeframe=timeframe,
                                      atr_value=atr_value, current=current))
        tolerance = max(atr_value * .15, abs(current) * .0007)
        for kind, equal_kind in (("SWING_HIGH", "EQUAL_HIGHS"), ("SWING_LOW", "EQUAL_LOWS")):
            candidates = [point for point in points[-40:] if point["kind"] == kind]
            for first, second in zip(candidates, candidates[1:]):
                if abs(first["price"] - second["price"]) <= tolerance:
                    levels.append(self._level(
                        frame, kind=equal_kind,
                        price=statistics.fmean((first["price"], second["price"])),
                        index=second["index"], timeframe=timeframe,
                        atr_value=atr_value, current=current,
                    ))

        timestamps = _timestamp_series(frame)
        if timestamps is not None and timestamps.notna().all() and len(frame) > 2:
            dated = frame.copy()
            dated["_timestamp"] = list(timestamps.reset_index(drop=True))
            dated["_day"] = dated["_timestamp"].dt.date
            days = list(dict.fromkeys(dated["_day"]))
            if len(days) >= 2:
                previous = dated[dated["_day"] == days[-2]]
                for kind, price in (("PREVIOUS_DAY_HIGH", float(previous["high"].max())),
                                    ("PREVIOUS_DAY_LOW", float(previous["low"].min()))):
                    levels.append(self._level(frame, kind=kind, price=price, index=max(0, len(frame) - len(previous) - 1),
                                              timeframe="1d", atr_value=atr_value, current=current))
            hour = int(dated.iloc[-1]["_timestamp"].hour)
            bounds = (0, 8) if hour < 8 else (8, 13) if hour < 13 else (13, 21) if hour < 21 else (21, 24)
            session = dated[(dated["_day"] == days[-1]) &
                            (dated["_timestamp"].dt.hour >= bounds[0]) &
                            (dated["_timestamp"].dt.hour < bounds[1])].iloc[:-1]
            if not session.empty:
                for kind, price in (("SESSION_HIGH", float(session["high"].max())),
                                    ("SESSION_LOW", float(session["low"].min()))):
                    levels.append(self._level(frame, kind=kind, price=price, index=max(0, len(frame) - len(session) - 1),
                                              timeframe=timeframe, atr_value=atr_value, current=current))

        if current > 0:
            step = 10 ** (math.floor(math.log10(current)) - 1)
            for price in sorted({math.floor(current / step) * step, round(current / step) * step,
                                 math.ceil(current / step) * step}):
                if price > 0:
                    levels.append(self._level(frame, kind="ROUND_LEVEL", price=price, index=len(frame) - 1,
                                              timeframe=timeframe, atr_value=atr_value, current=current))

        for index in range(max(2, len(frame) - 120), len(frame)):
            first, third = frame.iloc[index - 2], frame.iloc[index]
            zones: list[tuple[float, float]] = []
            if float(third["low"]) > float(first["high"]):
                zones.append((float(first["high"]), float(third["low"])))
            elif float(third["high"]) < float(first["low"]):
                zones.append((float(third["high"]), float(first["low"])))
            for lower, upper in zones:
                later = frame.iloc[index + 1:]
                mitigated = bool(not later.empty and ((later["low"] <= upper) & (later["high"] >= lower)).sum() >= 2)
                if not mitigated:
                    levels.append(self._level(frame, kind="FVG_LOW", price=lower, index=index,
                                              timeframe=timeframe, atr_value=atr_value, current=current))
                    levels.append(self._level(frame, kind="FVG_HIGH", price=upper, index=index,
                                              timeframe=timeframe, atr_value=atr_value, current=current))

        levels = sorted(levels, key=lambda item: (item["price"], item["type"], item["source_index"]))
        tolerance = max(atr_value * .22, abs(current) * .001)
        groups: list[list[dict[str, Any]]] = []
        for level in levels:
            if not groups or abs(level["price"] - statistics.fmean(x["price"] for x in groups[-1])) > tolerance:
                groups.append([level])
            else:
                groups[-1].append(level)
        clusters = []
        for group in groups:
            family_best: dict[str, float] = {}
            for item in group:
                family_best[item["family"]] = max(family_best.get(item["family"], 0), item["liquidity_significance"])
            independent = len(family_best)
            center = statistics.fmean(item["price"] for item in group)
            quality = _clip(statistics.fmean(family_best.values()) + max(0, independent - 1) * 7)
            clusters.append({
                "cluster_id": _checksum({"timeframe": timeframe, "center": round(center, 8),
                                         "families": sorted(family_best)})[:16],
                "price": round(center, 12), "lower": round(min(item["price"] for item in group), 12),
                "upper": round(max(item["price"] for item in group), 12),
                "types": sorted({item["type"] for item in group}),
                "independent_families": sorted(family_best), "independent_family_count": independent,
                "raw_representation_count": len(group), "quality": quality,
                "distance_pct": round(abs(current - center) / max(abs(current), 1e-12) * 100, 4),
                "side": "ABOVE" if center > current else "BELOW",
                "swept": all(item["swept"] for item in group),
                "fresh": all(item["fresh"] for item in group),
            })
        clusters = sorted(clusters, key=lambda item: (-item["quality"], item["distance_pct"]))[:24]
        retained_ids = {identifier for cluster in clusters for identifier in cluster["types"]}
        compact_levels = sorted((item for item in levels if item["type"] in retained_ids),
                                key=lambda item: (-item["liquidity_significance"], item["distance_pct"]))[:40]
        return {
            "version": LEVEL_VERSION, "status": "AVAILABLE", "current_price": current,
            "atr": atr_value, "levels": compact_levels, "clusters": clusters,
            "cluster_tolerance": tolerance,
            "double_counting_control": "MAX_WITHIN_FAMILY_THEN_INDEPENDENT_FAMILY_BONUS",
        }


class LiquidityMapEngine:
    @staticmethod
    def analyze(level_map: Mapping[str, Any]) -> dict[str, Any]:
        price = _number(level_map.get("current_price"))
        clusters = list(level_map.get("clusters") or [])
        candidates = []
        for cluster in clusters:
            consumed = bool(cluster.get("swept"))
            significance = _number(cluster.get("quality"))
            proximity = max(0.0, 100 - _number(cluster.get("distance_pct")) * 20)
            attraction = _clip(significance * .65 + proximity * .35 - (30 if consumed else 0))
            candidates.append({
                "cluster_id": cluster.get("cluster_id"), "price": cluster.get("price"),
                "side": cluster.get("side"), "significance": significance,
                "proximity_score": _clip(proximity), "attraction_score": attraction,
                "fresh": bool(cluster.get("fresh")), "swept": consumed,
                "state": "CONSUMED" if consumed else "UNRESOLVED",
                "types": cluster.get("types") or [],
            })
        above = sorted((item for item in candidates if item["side"] == "ABOVE"),
                       key=lambda item: (-item["attraction_score"], abs(_number(item["price"]) - price)))[:8]
        below = sorted((item for item in candidates if item["side"] == "BELOW"),
                       key=lambda item: (-item["attraction_score"], abs(_number(item["price"]) - price)))[:8]
        strongest = max(candidates, key=lambda item: item["attraction_score"], default=None)
        return {
            "version": "liquidity-map-v1", "above": above, "below": below,
            "likely_attractor": strongest, "unresolved_count": sum(item["state"] == "UNRESOLVED" for item in candidates),
            "consumed_count": sum(item["state"] == "CONSUMED" for item in candidates),
            "actor_identity_claimed": False,
        }


class StructureQualityEngine:
    @staticmethod
    def analyze(frame: pd.DataFrame, level_map: Mapping[str, Any]) -> dict[str, Any]:
        if len(frame) < 8:
            return {"status": "UNAVAILABLE", "break": "UNKNOWN", "sweep": "AMBIGUOUS"}
        atr_value = max(_number(level_map.get("atr")), 1e-12)
        points = _swing_points(frame)
        previous = [point for point in points if point["index"] <= len(frame) - 3]
        high_point = next((point for point in reversed(previous) if point["kind"] == "SWING_HIGH"), None)
        low_point = next((point for point in reversed(previous) if point["kind"] == "SWING_LOW"), None)
        last = frame.iloc[-1]
        close, high, low = map(float, (last["close"], last["high"], last["low"]))
        open_price = float(last["open"])
        body_atr = abs(close - open_price) / atr_value
        volume_median = max(_number(frame["volume"].tail(30).median()), 1e-12)
        volume_ratio = _number(last["volume"]) / volume_median
        break_type, direction, reference = "NO_BREAK", "NEUTRAL", None
        if high_point and close > high_point["price"]:
            break_type, direction, reference = "CLOSE_CONFIRMED_BREAK", "BULLISH", high_point["price"]
        elif low_point and close < low_point["price"]:
            break_type, direction, reference = "CLOSE_CONFIRMED_BREAK", "BEARISH", low_point["price"]
        elif high_point and high > high_point["price"] and close <= high_point["price"]:
            break_type, direction, reference = "WICK_BREAK", "BULLISH", high_point["price"]
        elif low_point and low < low_point["price"] and close >= low_point["price"]:
            break_type, direction, reference = "WICK_BREAK", "BEARISH", low_point["price"]
        displacement = "STRONG" if body_atr >= .9 else "MODERATE" if body_atr >= .45 else "WEAK"
        follow_through = False
        for offset in (3, 2):
            candidate = frame.iloc[-offset]
            later = frame.iloc[-offset + 1:]
            if high_point and float(candidate["close"]) > high_point["price"]:
                follow_through = bool((later["close"] > high_point["price"]).all())
                break
            if low_point and float(candidate["close"]) < low_point["price"]:
                follow_through = bool((later["close"] < low_point["price"]).all())
                break
        if break_type == "WICK_BREAK":
            sweep_class = "CLEAN_SWEEP" if body_atr <= .45 and volume_ratio >= .8 else "WEAK_SWEEP"
        elif break_type == "CLOSE_CONFIRMED_BREAK":
            sweep_class = "BREAKOUT_NOT_SWEEP"
        else:
            sweep_class = "AMBIGUOUS"
        quality = _clip(25 + (35 if break_type == "CLOSE_CONFIRMED_BREAK" else 15 if break_type == "WICK_BREAK" else 0)
                        + min(body_atr, 1.5) * 18 + min(volume_ratio, 2) * 8 + int(follow_through) * 12)
        return {
            "status": "AVAILABLE", "break": break_type, "direction": direction,
            "reference_level": reference, "internal_external": "EXTERNAL" if reference else "NONE",
            "displacement": displacement, "displacement_atr": round(body_atr, 4),
            "volume_ratio": round(volume_ratio, 4), "follow_through": follow_through,
            "retested": bool(reference and float(frame.iloc[-1]["low"]) <= reference <= float(frame.iloc[-1]["high"])),
            "reclaimed": break_type == "WICK_BREAK", "quality": quality,
            "sweep": sweep_class, "liquidity_sweep_mistaken_for_break": break_type == "WICK_BREAK",
        }


class ZoneQualityEngine:
    @staticmethod
    def analyze(frame: pd.DataFrame, side: str, level_map: Mapping[str, Any]) -> dict[str, Any]:
        if len(frame) < 20:
            return {"fvg": [], "order_blocks": [], "status": "UNAVAILABLE"}
        atr_value = max(_number(level_map.get("atr")), 1e-12)
        current = float(frame.iloc[-1]["close"])
        fvgs: list[dict[str, Any]] = []
        for index in range(max(2, len(frame) - 80), len(frame)):
            first, middle, third = frame.iloc[index - 2], frame.iloc[index - 1], frame.iloc[index]
            zone = None
            direction = None
            if float(third["low"]) > float(first["high"]):
                zone, direction = (float(first["high"]), float(third["low"])), "BULLISH"
            elif float(third["high"]) < float(first["low"]):
                zone, direction = (float(third["high"]), float(first["low"])), "BEARISH"
            if zone is None:
                continue
            later = frame.iloc[index + 1:]
            mitigations = int(((later["low"] <= zone[1]) & (later["high"] >= zone[0])).sum()) if not later.empty else 0
            impulse = abs(float(middle["close"]) - float(middle["open"])) / atr_value
            size_atr = (zone[1] - zone[0]) / atr_value
            quality = _clip(35 + min(impulse, 1.5) * 25 - max(0, size_atr - 1) * 12 - mitigations * 14)
            fvgs.append({"direction": direction, "lower": zone[0], "upper": zone[1],
                         "age_candles": len(frame) - 1 - index, "mitigation_count": mitigations,
                         "fresh": mitigations == 0, "size_atr": round(size_atr, 4),
                         "origin_displacement_atr": round(impulse, 4), "quality": quality,
                         "distance_pct": round(abs(statistics.fmean(zone) - current) / max(abs(current), 1e-12) * 100, 4)})
        order_blocks: list[dict[str, Any]] = []
        for index in range(max(1, len(frame) - 50), len(frame) - 2):
            candle, following = frame.iloc[index], frame.iloc[index + 1]
            body = float(candle["close"] - candle["open"])
            displacement = abs(float(following["close"] - following["open"])) / atr_value
            bullish = body < 0 and float(following["close"]) > float(candle["high"])
            bearish = body > 0 and float(following["close"]) < float(candle["low"])
            if not bullish and not bearish:
                continue
            lower, upper = float(candle["low"]), float(candle["high"])
            later = frame.iloc[index + 2:]
            mitigations = int(((later["low"] <= upper) & (later["high"] >= lower)).sum())
            quality = _clip(35 + min(displacement, 1.5) * 30 - mitigations * 15)
            order_blocks.append({"direction": "BULLISH" if bullish else "BEARISH",
                                 "lower": lower, "upper": upper, "fresh": mitigations == 0,
                                 "mitigation_count": mitigations, "origin_displacement_atr": round(displacement, 4),
                                 "quality": quality, "age_candles": len(frame) - 1 - index})
        return {"status": "AVAILABLE", "fvg": sorted(fvgs, key=lambda item: (-item["quality"], item["distance_pct"]))[:8],
                "order_blocks": sorted(order_blocks, key=lambda item: (-item["quality"], item["age_candles"]))[:8]}


class MomentumTrendEngine:
    @staticmethod
    def analyze(frame: pd.DataFrame) -> tuple[dict[str, Any], dict[str, Any]]:
        if len(frame) < 30:
            unknown = {"state": "UNKNOWN", "score": 0, "reason_codes": ["INSUFFICIENT_HISTORY"]}
            return unknown, {"state": "UNKNOWN", "score": 0}
        close = frame["close"].astype(float)
        atr_series = _atr(frame)
        atr_value = max(_number(atr_series.iloc[-1]), abs(float(close.iloc[-1])) * .001)
        returns = close.pct_change()
        velocity = _number(returns.tail(3).mean())
        prior_velocity = _number(returns.iloc[-8:-3].mean())
        acceleration = velocity - prior_velocity
        rsi_series = _rsi(close)
        rsi_now = _number(rsi_series.iloc[-1], 50)
        rsi_slope = rsi_now - _number(rsi_series.iloc[-4], 50)
        fast = close.ewm(span=12, adjust=False).mean()
        slow = close.ewm(span=26, adjust=False).mean()
        histogram = (fast - slow) - (fast - slow).ewm(span=9, adjust=False).mean()
        histogram_now = _number(histogram.iloc[-1])
        histogram_change = histogram_now - _number(histogram.iloc[-4])
        body = (frame["close"] - frame["open"]).abs().tail(8).sum()
        ranges = (frame["high"] - frame["low"]).abs().tail(8).sum()
        body_efficiency = _number(body / max(ranges, 1e-12))
        volume_now = _number(frame["volume"].tail(3).mean())
        volume_base = max(_number(frame["volume"].iloc[-30:-3].median()), 1e-12)
        volume_ratio = volume_now / volume_base
        travelled_atr = abs(float(close.iloc[-1] - close.iloc[-10])) / atr_value
        recent_highs = frame["high"].tail(8)
        recent_lows = frame["low"].tail(8)
        failed_continuations = int((recent_highs.diff() <= 0).tail(4).sum()) if velocity >= 0 else int((recent_lows.diff() >= 0).tail(4).sum())
        same_direction = velocity * prior_velocity > 0
        decelerating = same_direction and abs(velocity) < abs(prior_velocity) * .65
        exhausting = decelerating and (failed_continuations >= 2 or abs(rsi_slope) < 1) and travelled_atr >= 1.5
        reversing = velocity * prior_velocity < 0 and abs(velocity) >= abs(prior_velocity) * .5
        conflicted = (velocity > 0) != (histogram_now > 0) or (velocity > 0) != (rsi_slope > 0)
        if reversing:
            state = "REVERSING"
        elif exhausting:
            state = "EXHAUSTING"
        elif decelerating:
            state = "DECELERATING"
        elif conflicted:
            state = "CONFLICTED"
        elif abs(acceleration) > max(abs(prior_velocity) * .6, .0002) and same_direction:
            state = "ACCELERATING"
        elif abs(velocity) > max(abs(returns.tail(30).median()) * 1.5, .0002):
            state = "STRONG"
        else:
            state = "HEALTHY"
        direction = "BULLISH" if velocity > 0 else "BEARISH" if velocity < 0 else "NEUTRAL"
        score = _clip(50 + min(abs(velocity) * 10000, 25) + min(body_efficiency * 20, 15)
                      + min(volume_ratio, 2) * 5 - failed_continuations * 5 - (15 if conflicted else 0))
        momentum = {
            "state": state, "direction": direction, "score": score,
            "velocity": round(velocity, 8), "acceleration": round(acceleration, 8),
            "rsi": round(rsi_now, 3), "rsi_slope": round(rsi_slope, 3),
            "macd_histogram": round(histogram_now, 10), "macd_histogram_change": round(histogram_change, 10),
            "candle_body_efficiency": round(body_efficiency, 4), "volume_ratio": round(volume_ratio, 4),
            "distance_travelled_atr": round(travelled_atr, 4), "failed_continuations": failed_continuations,
        }
        ema20 = close.ewm(span=20, adjust=False).mean()
        ema50 = close.ewm(span=50, adjust=False).mean()
        extension = abs(float(close.iloc[-1] - ema20.iloc[-1])) / atr_value
        points = _swing_points(frame.tail(100).reset_index(drop=True))
        legs = min(8, max(1, len(points) // 2))
        trend_direction = "BULLISH" if float(ema20.iloc[-1]) > float(ema50.iloc[-1]) else "BEARISH"
        aligned = direction == trend_direction
        if state in {"EXHAUSTING", "REVERSING"} and extension >= 1.5:
            maturity = "EXHAUSTED"
        elif extension >= 2.5 or legs >= 6:
            maturity = "EXTENDED"
        elif legs >= 4 or extension >= 1.5:
            maturity = "MATURE"
        elif legs >= 2:
            maturity = "DEVELOPING"
        else:
            maturity = "EARLY"
        trend = {"state": maturity, "direction": trend_direction, "legs": legs,
                 "extension_atr": round(extension, 4), "momentum_aligned": aligned,
                 "failed_continuations": failed_continuations,
                 "continuation_penalty": 30 if maturity == "EXHAUSTED" else 18 if maturity == "EXTENDED" else 0}
        return momentum, trend


class ReversalResearchEngine:
    @staticmethod
    def analyze(frame: pd.DataFrame, timeframe: str, momentum: Mapping[str, Any],
                structure: Mapping[str, Any], trend: Mapping[str, Any]) -> dict[str, Any]:
        if len(frame) < 30:
            return {"pump": {"state": "UNAVAILABLE"}, "dump": {"state": "UNAVAILABLE"}}
        periods = max(2, min(len(frame) - 1, round(86400 / TIMEFRAME_SECONDS.get(timeframe, 3600))))
        close = frame["close"].astype(float)
        reference = float(close.iloc[-periods - 1])
        move_pct = (float(close.iloc[-1]) / max(abs(reference), 1e-12) - 1) * 100
        atr_value = max(_number(_atr(frame).iloc[-1]), abs(float(close.iloc[-1])) * .001)
        move_atr = abs(float(close.iloc[-1] - reference)) / atr_value
        window = frame.tail(periods + 1)
        high, low, current = float(window["high"].max()), float(window["low"].min()), float(close.iloc[-1])
        distance_high_pct = max(0.0, (high - current) / max(abs(high), 1e-12) * 100)
        distance_low_pct = max(0.0, (current - low) / max(abs(low), 1e-12) * 100)
        tolerance = atr_value * .2
        failed_high_retests = int(((window["high"] >= high - tolerance) & (window["close"] < high - tolerance * .3)).sum())
        failed_low_retests = int(((window["low"] <= low + tolerance) & (window["close"] > low + tolerance * .3)).sum())
        extreme = abs(move_pct) >= _number(os.getenv("REVERSAL_EXTREME_MOVE_PCT", "12"), 12) or move_atr >= 6
        rolling_moves = (close.pct_change(periods).abs() * 100).dropna()
        move_percentile = (float((rolling_moves <= abs(move_pct)).mean() * 100)
                           if not rolling_moves.empty else None)
        high_index = int(window["high"].idxmax())
        low_index = int(window["low"].idxmin())
        time_below_high = max(0, len(frame) - 1 - high_index)
        time_above_low = max(0, len(frame) - 1 - low_index)
        volume_median = max(_number(window["volume"].median()), 1e-12)
        volume_climax_ratio = _number(window["volume"].max()) / volume_median
        volume_decay_ratio = _number(window["volume"].tail(5).mean()) / max(_number(window["volume"].max()), 1e-12)
        momentum_state = str(momentum.get("state") or "UNKNOWN")
        break_direction = str(structure.get("direction") or "NEUTRAL")
        pump_confirmed = extreme and move_pct > 0 and momentum_state in {"DECELERATING", "EXHAUSTING", "REVERSING"} and (
            failed_high_retests >= 2 or break_direction == "BEARISH")
        dump_confirmed = extreme and move_pct < 0 and momentum_state in {"DECELERATING", "EXHAUSTING", "REVERSING"} and (
            failed_low_retests >= 2 or break_direction == "BULLISH")
        pump_early = extreme and move_pct > 0 and distance_high_pct <= max(3.0, abs(move_pct) * .25)
        dump_early = extreme and move_pct < 0 and distance_low_pct <= max(3.0, abs(move_pct) * .25)
        pump_continuation = extreme and move_pct > 0 and momentum_state in {"ACCELERATING", "STRONG"} and break_direction != "BEARISH"
        dump_continuation = extreme and move_pct < 0 and momentum_state in {"ACCELERATING", "STRONG"} and break_direction != "BULLISH"
        def candidate(kind: str, confirmed: bool, early: bool, continuation: bool,
                      failed_retests: int, distance: float, invalidation: float,
                      time_away_from_extreme: int) -> dict[str, Any]:
            if continuation:
                state = f"{kind}_CONTINUATION_RISK"
            elif confirmed:
                state = f"{kind}_CONFIRMED"
            elif early:
                state = f"{kind}_EARLY"
            else:
                state = f"{kind}_INVALID"
            evidence = [
                f"24h move {move_pct:+.2f}% ({move_atr:.2f} ATR)",
                f"failed extreme retests {failed_retests}",
                f"momentum {momentum_state}", f"structure {break_direction}",
            ]
            return {"state": state, "eligible": bool(confirmed or early), "confirmed": confirmed,
                    "continuation_risk": continuation, "move_24h_pct": round(move_pct, 4),
                    "move_atr": round(move_atr, 4), "distance_from_extreme_pct": round(distance, 4),
                    "move_percentile": None if move_percentile is None else round(move_percentile, 3),
                    "time_away_from_extreme_candles": time_away_from_extreme,
                    "failed_retests": failed_retests, "trend_maturity": trend.get("state"),
                    "volume_climax_ratio": round(volume_climax_ratio, 4),
                    "volume_decay_ratio": round(volume_decay_ratio, 4),
                    "hypothetical_invalidation": invalidation, "evidence": evidence,
                    "mode": "SHADOW_RESEARCH_ONLY", "martingale": False}
        return {"pump": candidate("PUMP_REVERSAL", pump_confirmed, pump_early, pump_continuation,
                                  failed_high_retests, distance_high_pct, high, time_below_high),
                "dump": candidate("DUMP_REVERSAL", dump_confirmed, dump_early, dump_continuation,
                                  failed_low_retests, distance_low_pct, low, time_above_low)}


class OrderBookMicrostructureEngine:
    """Normalizes bounded book interactions; a single wall is never treated as truth."""

    @staticmethod
    def _book(snapshot: Mapping[str, Any], max_levels: int) -> dict[str, Any] | None:
        def levels(key: str) -> list[tuple[float, float]]:
            result = []
            for item in list(snapshot.get(key) or [])[:max_levels]:
                if isinstance(item, Mapping):
                    price, size = _number(item.get("price")), _number(item.get("size") or item.get("quantity"))
                elif isinstance(item, (list, tuple)) and len(item) >= 2:
                    price, size = _number(item[0]), _number(item[1])
                else:
                    continue
                if price > 0 and size > 0:
                    result.append((price, size))
            return result
        bids, asks = levels("bids"), levels("asks")
        if not bids or not asks:
            return None
        best_bid, best_ask = max(price for price, _ in bids), min(price for price, _ in asks)
        if best_ask <= best_bid:
            return None
        mid = (best_bid + best_ask) / 2
        return {"bids": sorted(bids, reverse=True), "asks": sorted(asks), "mid": mid,
                "spread_pct": (best_ask - best_bid) / mid * 100,
                "aggressive_buy_volume": _number(snapshot.get("aggressive_buy_volume")),
                "aggressive_sell_volume": _number(snapshot.get("aggressive_sell_volume")),
                "timestamp": snapshot.get("timestamp") or snapshot.get("captured_at")}

    def analyze(self, snapshots: Iterable[Mapping[str, Any]], *, max_levels: int = 50,
                max_snapshots: int = 12) -> dict[str, Any]:
        books = [book for raw in list(snapshots)[-max_snapshots:]
                 if (book := self._book(raw, max(5, min(max_levels, 100)))) is not None]
        if not books:
            return {"version": MICROSTRUCTURE_VERSION, "status": "UNAVAILABLE",
                    "reason_codes": ["NO_VALID_ORDER_BOOK_SNAPSHOTS"], "classifications": ["UNKNOWN"]}
        latest = books[-1]
        bands: dict[str, dict[str, float]] = {}
        for pct in (.1, .25, .5, 1.0):
            lower, upper = latest["mid"] * (1 - pct / 100), latest["mid"] * (1 + pct / 100)
            bid_depth = sum(size for price, size in latest["bids"] if price >= lower)
            ask_depth = sum(size for price, size in latest["asks"] if price <= upper)
            total = bid_depth + ask_depth
            bands[str(pct)] = {"bid_depth": round(bid_depth, 8), "ask_depth": round(ask_depth, 8),
                               "imbalance": round((bid_depth - ask_depth) / total, 5) if total else 0.0}
        all_sizes = [size for book in books for side in ("bids", "asks") for _, size in book[side]]
        wall_threshold = max(statistics.median(all_sizes) * 4, np.percentile(all_sizes, 92)) if all_sizes else float("inf")
        observations: dict[tuple[str, int], list[tuple[int, float, float, float]]] = defaultdict(list)
        price_tolerance = max(latest["mid"] * .0005, 1e-12)
        for book_index, book in enumerate(books):
            for side in ("bids", "asks"):
                for price, size in book[side]:
                    if size >= wall_threshold:
                        observations[(side, round(price / price_tolerance))].append((book_index, price, size, book["mid"]))
        walls = []
        classifications: set[str] = set()
        for (side, _), events in observations.items():
            seen = len({event[0] for event in events})
            first, last = events[0], events[-1]
            persistence = seen / len(books)
            # Mid-price proximity alone is not a touch: the best ask/bid is
            # mechanically near mid. Require price to approach the wall from
            # the executable side within a narrow fraction of cluster tolerance.
            touched = any(
                (side == "asks" and mid >= price - price_tolerance * .25) or
                (side == "bids" and mid <= price + price_tolerance * .25)
                for _, price, _, mid in events
            )
            present_latest = last[0] == len(books) - 1
            if persistence >= .6 and present_latest:
                state = "PERSISTENT_WALL"
            elif seen == 1 and not present_latest:
                state = "PULLED_WALL"
            else:
                sizes = [event[2] for event in events]
                state = "REPLENISHING_WALL" if touched and min(sizes) >= max(sizes) * .65 else "STATIC_WALL"
            crossed = (side == "asks" and latest["mid"] > last[1]) or (side == "bids" and latest["mid"] < last[1])
            if crossed:
                state = "SWEPT_WALL"
            spoof_like = state == "PULLED_WALL" and not touched
            if spoof_like:
                classifications.add("POSSIBLE_SPOOF")
            classifications.add(state)
            walls.append({"side": "ASK" if side == "asks" else "BID", "price": round(last[1], 12),
                          "relative_size": round(last[2] / max(statistics.median(all_sizes), 1e-12), 3),
                          "persistence_ratio": round(persistence, 3), "touched": touched,
                          "state": state, "spoof_like": spoof_like})
        mid_change = (latest["mid"] / books[0]["mid"] - 1) * 100 if len(books) > 1 else 0.0
        latest_imbalance = bands["0.5"]["imbalance"]
        total_depths = [sum(size for side in ("bids", "asks") for _, size in book[side]) for book in books]
        depth_turnover = (abs(total_depths[-1] - total_depths[0]) / max(total_depths[0], 1e-12)
                          if len(total_depths) > 1 else 0.0)
        absorption = "UNCONFIRMED"
        aggressive_buy = sum(book["aggressive_buy_volume"] for book in books)
        aggressive_sell = sum(book["aggressive_sell_volume"] for book in books)
        executed_flow_available = aggressive_buy > 0 or aggressive_sell > 0
        if executed_flow_available and len(books) >= 3 and abs(mid_change) <= .05:
            if aggressive_buy >= aggressive_sell * 1.5 and aggressive_buy > 0:
                absorption = "POSSIBLE_SELL_ABSORPTION"
            elif aggressive_sell >= aggressive_buy * 1.5 and aggressive_sell > 0:
                absorption = "POSSIBLE_BUY_ABSORPTION"
        if absorption != "UNCONFIRMED":
            classifications.add("ABSORPTION")
        if not classifications:
            classifications.add("UNKNOWN")
        behavior_labels: set[str] = set()
        for wall in walls:
            prefix = "ASK" if wall["side"] == "ASK" else "BID"
            label = {
                "PERSISTENT_WALL": f"PERSISTENT_{prefix}_WALL",
                "PULLED_WALL": f"{prefix}_WALL_REMOVED",
                "REPLENISHING_WALL": f"{prefix}_REPLENISHMENT",
                "SWEPT_WALL": f"{prefix}_SWEEP",
            }.get(wall["state"])
            if label:
                behavior_labels.add(label)
            if wall["spoof_like"]:
                behavior_labels.add("SPOOF_LIKE_REMOVAL_PATTERN")
        if latest_imbalance >= .2:
            behavior_labels.add("DEPTH_IMBALANCE_BID")
        elif latest_imbalance <= -.2:
            behavior_labels.add("DEPTH_IMBALANCE_ASK")
        if not behavior_labels:
            behavior_labels.add("MICROSTRUCTURE_NEUTRAL" if len(books) >= 3 else "INSUFFICIENT_HISTORY")
        interaction_quality = _clip(min(len(books) / 5, 1) * 35 + min(len(walls), 3) * 10
                                    + sum(wall["touched"] for wall in walls) * 12)
        persistence_quality = _clip(statistics.fmean(
            [wall["persistence_ratio"] * 100 for wall in walls]
        ) if walls else min(len(books) / 5, 1) * 55)
        spread_quality = _clip(100 - latest["spread_pct"] * 2200)
        depth_stability = _clip(100 - min(depth_turnover, 1) * 100)
        microstructure_quality = _clip(
            interaction_quality * .40 + persistence_quality * .20
            + spread_quality * .20 + depth_stability * .20
        )
        imbalance_history = []
        for book in books:
            bid_depth = sum(size for _, size in book["bids"])
            ask_depth = sum(size for _, size in book["asks"])
            total = bid_depth + ask_depth
            imbalance_history.append((bid_depth - ask_depth) / total if total else 0.0)
        imbalance_trend = "STABLE"
        if len(imbalance_history) >= 3:
            delta = statistics.fmean(imbalance_history[-2:]) - statistics.fmean(imbalance_history[:2])
            imbalance_trend = "BID_STRENGTHENING" if delta >= .12 else "ASK_STRENGTHENING" if delta <= -.12 else "STABLE"
        best_bid = latest["bids"][0][0]
        best_ask = latest["asks"][0][0]
        return {
            "version": MICROSTRUCTURE_VERSION, "status": "AVAILABLE", "sample_count": len(books),
            "feature_schema_version": MICROSTRUCTURE_VERSION,
            "observation_timestamp": latest.get("timestamp"),
            "source": "BINGX_PUBLIC_FUTURES_DEPTH",
            "freshness": "FRESH", "stale_state": False,
            "levels_per_side_max": max_levels, "mid_price": latest["mid"],
            "best_bid": best_bid, "best_ask": best_ask,
            "spread_pct": round(latest["spread_pct"], 5),
            "spread_bps": round(latest["spread_pct"] * 100, 3), "depth_bands": bands,
            "walls": sorted(walls, key=lambda wall: (-wall["persistence_ratio"], -wall["relative_size"]))[:12],
            "classifications": sorted(classifications), "absorption_inference": absorption,
            "behavior_labels": sorted(behavior_labels),
            "executed_flow_available": executed_flow_available,
            "actor_identity_claimed": False, "interaction_quality": interaction_quality,
            "microstructure_quality": microstructure_quality,
            "quality_components": {"interaction": interaction_quality,
                                   "persistence": persistence_quality,
                                   "spread": spread_quality,
                                   "depth_stability": depth_stability},
            "imbalance_trend": imbalance_trend,
            "mid_change_pct": round(mid_change, 5), "depth_turnover": round(depth_turnover, 5),
            "raw_book_persisted": False, "spoofing_caveat": "Resting liquidity is untrusted until interaction confirms it.",
        }


@dataclass(slots=True)
class BoundedMicrostructureBuffer:
    max_symbols: int = 12
    max_snapshots_per_symbol: int = 12
    _books: dict[str, deque[dict[str, Any]]] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        self.max_symbols = max(1, min(int(self.max_symbols), 50))
        self.max_snapshots_per_symbol = max(3, min(int(self.max_snapshots_per_symbol), 60))

    def ingest(self, symbol: str, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        key = str(symbol).upper()
        if key not in self._books and len(self._books) >= self.max_symbols:
            oldest = next(iter(self._books))
            del self._books[oldest]
        buffer = self._books.setdefault(key, deque(maxlen=self.max_snapshots_per_symbol))
        buffer.append(dict(snapshot))
        return OrderBookMicrostructureEngine().analyze(buffer,
            max_levels=int(os.getenv("MICROSTRUCTURE_MAX_LEVELS", "50")),
            max_snapshots=self.max_snapshots_per_symbol)

    def symbols(self) -> tuple[str, ...]:
        return tuple(self._books)


class MarketStoryEngine:
    @staticmethod
    def analyze(*, structure: Mapping[str, Any], momentum: Mapping[str, Any],
                trend: Mapping[str, Any], reversal: Mapping[str, Any],
                level_map: Mapping[str, Any], previous_state: str | None = None) -> dict[str, Any]:
        momentum_state = str(momentum.get("state") or "UNKNOWN")
        maturity = str(trend.get("state") or "UNKNOWN")
        break_type = str(structure.get("break") or "UNKNOWN")
        sweep = str(structure.get("sweep") or "AMBIGUOUS")
        pump = str((reversal.get("pump") or {}).get("state") or "")
        dump = str((reversal.get("dump") or {}).get("state") or "")
        facts: list[str] = []
        if "CONFIRMED" in pump:
            state = "PUMP_EXHAUSTION"
            facts.extend(["extreme upward expansion is no longer producing clean continuation",
                          "momentum and/or structure provide reversal evidence"])
        elif "CONFIRMED" in dump:
            state = "DUMP_EXHAUSTION"
            facts.extend(["extreme downward expansion is no longer producing clean continuation",
                          "momentum and/or structure provide reversal evidence"])
        elif sweep == "CLEAN_SWEEP":
            state = "LIQUIDITY_SWEEP"
            facts.append("price crossed a confirmed swing and closed back through it")
        elif break_type == "CLOSE_CONFIRMED_BREAK" and structure.get("displacement") in {"MODERATE", "STRONG"}:
            state = "BREAKOUT_ATTEMPT"
            facts.append("structure broke on a candle close with displacement")
        elif maturity in {"EXTENDED", "EXHAUSTED"} and momentum_state in {"DECELERATING", "EXHAUSTING", "REVERSING"}:
            state = "MOMENTUM_EXHAUSTION"
            facts.extend([f"trend maturity is {maturity.lower()}", f"momentum is {momentum_state.lower()}"])
        elif momentum_state in {"ACCELERATING", "STRONG"} and trend.get("momentum_aligned"):
            state = "TREND_CONTINUATION"
            facts.append("trend and momentum remain aligned")
        elif not level_map.get("clusters"):
            state = "UNCERTAIN"
            facts.append("reliable structural levels are unavailable")
        elif break_type == "NO_BREAK" and momentum_state in {"CONFLICTED", "HEALTHY"}:
            state = "RANGE" if len(level_map.get("clusters") or []) >= 2 else "UNCERTAIN"
            facts.append("price remains between unresolved structural areas")
        else:
            state = "PULLBACK" if trend.get("direction") in {"BULLISH", "BEARISH"} else "UNCERTAIN"
            facts.append("market evidence is mixed and no fresh confirmed break dominates")
        return {"version": STORY_VERSION, "state": state,
                "previous_state": previous_state or "UNKNOWN",
                "transition": f"{previous_state}->{state}" if previous_state and previous_state != state else "UNCHANGED_OR_UNKNOWN",
                "facts": facts[:6], "grounding": "DETERMINISTIC_DECISION_TIME_FACTS",
                "future_data_used": False}


class SignalQualityEngine:
    @staticmethod
    def _aggregate(factors: Iterable[tuple[str, float]]) -> tuple[float | None, list[dict[str, Any]]]:
        normalized = [{"factor": label, "score": _clip(score)} for label, score in factors]
        if not normalized:
            return None, []
        scores = [item["score"] for item in normalized]
        aggregate = max(scores) * .65 + statistics.fmean(scores) * .35
        return _clip(aggregate), normalized

    def analyze(self, *, side: str, plan: Mapping[str, Any], data_quality: Mapping[str, Any],
                level_map: Mapping[str, Any], liquidity: Mapping[str, Any], structure: Mapping[str, Any],
                zones: Mapping[str, Any], momentum: Mapping[str, Any], trend: Mapping[str, Any],
                story: Mapping[str, Any], microstructure: Mapping[str, Any] | None,
                relative_strength: Mapping[str, Any], funding_oi: Mapping[str, Any]) -> dict[str, Any]:
        price = _number(plan.get("entry") or level_map.get("current_price"))
        stop = _number(plan.get("stop"))
        atr_value = max(_number(level_map.get("atr")), abs(price) * .001, 1e-12)
        direction = "BULLISH" if side == "LONG" else "BEARISH"
        geometry = (side == "LONG" and 0 < stop < price) or (side == "SHORT" and stop > price > 0)
        stop_atr = abs(price - stop) / atr_value if geometry else 0.0
        structural = [item for item in level_map.get("levels") or []
                      if item.get("type") == ("SWING_LOW" if side == "LONG" else "SWING_HIGH")]
        expected_side = [item for item in structural if (_number(item.get("price")) < price if side == "LONG" else _number(item.get("price")) > price)]
        nearest = min(expected_side, key=lambda item: abs(_number(item.get("price")) - stop), default=None)
        structural_distance_atr = abs(_number((nearest or {}).get("price")) - stop) / atr_value if nearest else None
        crowd_cluster = min((item for item in level_map.get("clusters") or []),
                            key=lambda item: abs(_number(item.get("price")) - stop), default=None)
        crowd_distance_atr = abs(_number((crowd_cluster or {}).get("price")) - stop) / atr_value if crowd_cluster else None
        invalidation_score = 0.0 if not geometry else 55.0
        if geometry and .45 <= stop_atr <= 3.0:
            invalidation_score += 18
        if structural_distance_atr is not None and structural_distance_atr <= .35:
            invalidation_score += 20
        if crowd_distance_atr is not None and crowd_distance_atr <= .12:
            invalidation_score -= 15
        invalidation = {
            "score": _clip(invalidation_score), "valid_geometry": geometry,
            "distance_atr": round(stop_atr, 4), "structural_reference": nearest,
            "structural_distance_atr": None if structural_distance_atr is None else round(structural_distance_atr, 4),
            "obvious_liquidity_exposure": bool(crowd_distance_atr is not None and crowd_distance_atr <= .12),
            "reason_codes": (["INVALID_STOP_GEOMETRY"] if not geometry else []) +
                            (["STOP_NEAR_CROWD_LIQUIDITY"] if crowd_distance_atr is not None and crowd_distance_atr <= .12 else []),
        }
        factors: dict[str, list[tuple[str, float]]] = {
            "MARKET_QUALITY": [("data quality", 90 if data_quality.get("status") == "GOOD" else 55),
                               ("story clarity", 75 if story.get("state") not in {"UNCERTAIN", "UNKNOWN"} else 35)],
            "STRUCTURE": [("break quality", _number(structure.get("quality"), 40))],
            "LIQUIDITY": [("unresolved liquidity clarity", min(95, 35 + int(liquidity.get("unresolved_count") or 0) * 8))],
            "LOCATION": [("nearest level proximity", max(20, 90 - min(([_number(x.get("distance_pct")) for x in level_map.get("clusters") or []] or [5])) * 18))],
            "MOMENTUM": [("momentum quality", _number(momentum.get("score"), 50)),
                         ("direction alignment", 82 if momentum.get("direction") == direction else 35)],
            "VOLATILITY": [("trend extension", 35 if trend.get("state") in {"EXTENDED", "EXHAUSTED"} else 75)],
            "MICROSTRUCTURE": ([("interaction quality", _number((microstructure or {}).get("microstructure_quality"), 0))]
                               if (microstructure or {}).get("status") == "AVAILABLE" else []),
            "HTF_CONTEXT": ([("hierarchy supplied", _number(plan.get("htf_context_score"), 0))]
                            if plan.get("htf_context_score") is not None else []),
            "RELATIVE_STRENGTH": ([("benchmark context", _number(relative_strength.get("quality"), 0))]
                                  if relative_strength.get("status") == "AVAILABLE" else []),
            "DERIVATIVES": ([("funding and open-interest context", 65)]
                            if funding_oi.get("status") == "AVAILABLE" else []),
            "INVALIDATION": [("thesis invalidation", invalidation["score"])],
            "TARGET_REALISM": [("planned RR realism", 80 if 1 <= _number(plan.get("rr")) <= 3 else 55 if _number(plan.get("rr")) <= 4 else 30)],
            "EXECUTION_COST": ([("cost coverage", _number(plan.get("execution_cost_quality"), 0))]
                               if plan.get("execution_cost_quality") is not None else []),
            "PORTFOLIO_CONTEXT": ([("advisory-only portfolio context", _number(plan.get("portfolio_context_quality"), 0))]
                                  if plan.get("portfolio_context_quality") is not None else []),
        }
        family_scores: dict[str, float | None] = {}
        raw_components: dict[str, list[dict[str, Any]]] = {}
        for family, items in factors.items():
            family_scores[family], raw_components[family] = self._aggregate(items)
        supporting: list[dict[str, Any]] = []
        contradicting: list[dict[str, Any]] = []
        uncertainties: list[str] = []
        critical: list[str] = []
        if structure.get("direction") == direction:
            supporting.append({"family": "STRUCTURE", "severity": "HIGH", "reason": "confirmed structure agrees with direction"})
        elif structure.get("direction") not in {"NEUTRAL", None, "UNKNOWN"}:
            contradicting.append({"family": "STRUCTURE", "severity": "HIGH", "reason": "confirmed structure opposes direction"})
        if momentum.get("direction") == direction and momentum.get("state") not in {"EXHAUSTING", "REVERSING"}:
            supporting.append({"family": "MOMENTUM", "severity": "MEDIUM", "reason": "momentum agrees without exhaustion"})
        elif momentum.get("direction") != direction or momentum.get("state") in {"EXHAUSTING", "REVERSING"}:
            contradicting.append({"family": "MOMENTUM", "severity": "HIGH", "reason": "momentum conflicts or is exhausting"})
        if trend.get("state") in {"EXTENDED", "EXHAUSTED"}:
            contradicting.append({"family": "VOLATILITY", "severity": "HIGH", "reason": f"trend is {str(trend.get('state')).lower()}"})
        if not geometry:
            critical.append("INVALID_STOP_GEOMETRY")
            contradicting.append({"family": "INVALIDATION", "severity": "CRITICAL", "reason": "stop does not invalidate the stated direction"})
        if data_quality.get("status") == "INVALID":
            critical.append("MARKET_DATA_INVALID")
            contradicting.append({"family": "MARKET_QUALITY", "severity": "CRITICAL", "reason": "market data is invalid"})
        if not microstructure or microstructure.get("status") != "AVAILABLE":
            uncertainties.append("ORDER_BOOK_UNAVAILABLE")
        if relative_strength.get("status") != "AVAILABLE":
            uncertainties.append("BENCHMARK_CONTEXT_UNAVAILABLE")
        if funding_oi.get("status") != "AVAILABLE":
            uncertainties.append("FUNDING_OR_OPEN_INTEREST_UNAVAILABLE")
        severity_penalties = {"LOW": 2, "MEDIUM": 6, "HIGH": 13, "CRITICAL": 35}
        contradiction_families: dict[str, list[float]] = defaultdict(list)
        for item in contradicting:
            contradiction_families[str(item.get("family") or "OTHER")].append(
                severity_penalties[str(item.get("severity") or "LOW")]
            )
        penalty = round(sum(
            max(values) + (sum(values) - max(values)) * .25
            for values in contradiction_families.values()
        ), 3)
        weights = {"MARKET_QUALITY": .13, "STRUCTURE": .12, "LIQUIDITY": .10, "LOCATION": .08,
                   "MOMENTUM": .10, "VOLATILITY": .06, "MICROSTRUCTURE": .07, "HTF_CONTEXT": .06,
                   "RELATIVE_STRENGTH": .04, "DERIVATIVES": .04, "INVALIDATION": .10,
                   "TARGET_REALISM": .06, "EXECUTION_COST": .03, "PORTFOLIO_CONTEXT": .01}
        available_weight = sum(weight for key, weight in weights.items() if family_scores.get(key) is not None)
        raw_score = (sum(float(family_scores[key]) * weights[key] for key in weights
                         if family_scores.get(key) is not None) / max(available_weight, 1e-12))
        overall = _clip(raw_score - min(penalty, 45))
        if critical:
            overall = min(overall, 35.0)
        market_parts = {"MARKET_QUALITY": .30, "LIQUIDITY": .20, "STRUCTURE": .20,
                        "VOLATILITY": .10, "MICROSTRUCTURE": .10, "EXECUTION_COST": .10}
        market_weight = sum(weight for key, weight in market_parts.items() if family_scores.get(key) is not None)
        market_quality = _clip(sum(float(family_scores[key]) * weight for key, weight in market_parts.items()
                                   if family_scores.get(key) is not None) / max(market_weight, 1e-12)
                               - min(penalty, 30))
        unavailable = [key for key, value in family_scores.items() if value is None]
        coverage = (len(family_scores) - len(unavailable)) / max(1, len(family_scores))
        data_confidence = _clip((95 if data_quality.get("status") == "GOOD" else 60) * (.65 + .35 * coverage))
        def family(name: str, fallback: float = 50.0) -> float:
            value = family_scores.get(name)
            return fallback if value is None else float(value)
        confidence = {
            "DATA_CONFIDENCE": data_confidence,
            "DIRECTION_CONFIDENCE": _clip(statistics.fmean([family("STRUCTURE"), family("MOMENTUM"), family("HTF_CONTEXT")])),
            "SETUP_CONFIDENCE": _clip(statistics.fmean([family("LIQUIDITY"), family("LOCATION"), family("STRUCTURE")])),
            "ENTRY_CONFIDENCE": _clip(statistics.fmean([family("LOCATION"), family("MICROSTRUCTURE")])),
            "INVALIDATION_CONFIDENCE": invalidation["score"],
            "TARGET_CONFIDENCE": family("TARGET_REALISM"),
            "EXECUTION_CONFIDENCE": _clip(statistics.fmean([family("EXECUTION_COST"), family("MICROSTRUCTURE")])),
            "OVERALL_QUALITY": overall,
        }
        family_count = sum(score is not None and score >= 60 for score in family_scores.values())
        diversity = _clip(family_count / max(1, len(family_scores) - len(unavailable)) * 100)
        setup_quality = _clip(
            family("STRUCTURE") * .30 + family("LIQUIDITY") * .25
            + family("LOCATION") * .20 + family("MOMENTUM") * .25
            - min(penalty, 30)
        )
        entry_parts = {"LOCATION": .32, "MICROSTRUCTURE": .28,
                       "INVALIDATION": .25, "EXECUTION_COST": .15}
        entry_weight = sum(weight for key, weight in entry_parts.items() if family_scores.get(key) is not None)
        entry_quality = _clip(sum(float(family_scores[key]) * weight for key, weight in entry_parts.items()
                                  if family_scores.get(key) is not None) / max(entry_weight, 1e-12)
                              - min(penalty, 25))
        execution_parts = {"INVALIDATION": .35, "TARGET_REALISM": .25,
                           "EXECUTION_COST": .25, "MICROSTRUCTURE": .15}
        execution_weight = sum(weight for key, weight in execution_parts.items() if family_scores.get(key) is not None)
        execution_quality = _clip(sum(float(family_scores[key]) * weight for key, weight in execution_parts.items()
                                      if family_scores.get(key) is not None) / max(execution_weight, 1e-12))
        return {
            "version": QUALITY_VERSION, "overall_quality": overall, "market_quality": market_quality,
            "setup_quality": setup_quality, "entry_quality": entry_quality,
            "execution_quality": execution_quality, "data_confidence": data_confidence,
            "quality_dimensions": {"setup": setup_quality, "entry": entry_quality,
                                   "market": market_quality, "execution": execution_quality,
                                   "data_confidence": data_confidence},
            "family_scores": family_scores, "normalized_components": dict(family_scores),
            "raw_components": raw_components,
            "family_aggregation": "INDEPENDENT_FAMILY_MAX_65_PLUS_MEAN_35_COVERAGE_NORMALIZED",
            "evidence_family_count": family_count, "evidence_diversity_score": diversity,
            "evidence_coverage": round(coverage, 4), "unavailable_families": unavailable,
            "evaluation_state": "EVALUATED",
            "supporting_evidence": supporting, "contradicting_evidence": contradicting,
            "critical_disqualifiers": critical, "uncertainties": uncertainties,
            "contradiction_penalty": penalty,
            "contradiction_aggregation": "MAX_PER_FAMILY_PLUS_25_PERCENT_CORRELATED_REMAINDER",
            "confidence": confidence, "invalidation": invalidation,
            "score_is_probability": False, "economic_authority": False,
        }


class MarketIntelligenceEngine:
    """Research-only market understanding built after the legacy decision is fixed."""

    def __init__(self) -> None:
        self.levels = LevelIntelligenceEngine()
        self.liquidity = LiquidityMapEngine()
        self.structure = StructureQualityEngine()
        self.zones = ZoneQualityEngine()
        self.reversals = ReversalResearchEngine()
        self.quality = SignalQualityEngine()

    @staticmethod
    def _relative_strength(frame: pd.DataFrame, benchmark: pd.DataFrame | None) -> dict[str, Any]:
        if benchmark is None or len(benchmark) < 3 or len(frame) < 3:
            return {"status": "UNAVAILABLE", "quality": 50, "reason": "benchmark series not supplied"}
        count = min(len(frame), len(benchmark), 25)
        asset_close = frame["close"].astype(float).iloc[-count:].reset_index(drop=True)
        bench_close = benchmark["close"].astype(float).iloc[-count:].reset_index(drop=True)
        asset = float(asset_close.iloc[-1] / asset_close.iloc[0] - 1) * 100
        bench = float(bench_close.iloc[-1] / bench_close.iloc[0] - 1) * 100
        spread = asset - bench
        asset_returns = asset_close.pct_change().dropna()
        bench_returns = bench_close.pct_change().dropna()
        correlation = float(asset_returns.corr(bench_returns)) if len(asset_returns) >= 3 else 0.0
        variance = float(bench_returns.var()) if len(bench_returns) >= 3 else 0.0
        beta = float(asset_returns.cov(bench_returns) / variance) if variance > 1e-16 else 0.0
        if spread > 2 and correlation < .75:
            state = "INDEPENDENT_STRENGTH"
        elif spread < -2 and correlation < .75:
            state = "INDEPENDENT_WEAKNESS"
        elif abs(beta) >= 1.25:
            state = "HIGH_BETA"
        elif abs(beta) <= .55:
            state = "DEFENSIVE_DECOUPLING"
        else:
            state = "MARKET_BETA"
        return {"status": "AVAILABLE", "asset_move_pct": round(asset, 4), "benchmark_move_pct": round(bench, 4),
                "relative_move_pct": round(spread, 4), "correlation": round(correlation, 4),
                "beta": round(beta, 4), "state": state,
                "benchmark_version": "btc-benchmark-v2",
                "quality": _clip(55 + min(abs(spread), 10) * 3 + min(count, 25) / 5)}

    @staticmethod
    def _funding_oi(context: Mapping[str, Any] | None) -> dict[str, Any]:
        context = context or {}
        funding = context.get("funding_rate")
        oi = context.get("open_interest")
        oi_change = context.get("open_interest_change_pct")
        if funding is None and oi is None and oi_change is None:
            return {"status": "UNAVAILABLE", "reason_codes": ["FUNDING_OI_NOT_SUPPLIED"],
                    "fabricated": False}
        price_change = context.get("price_change_pct")
        divergence = "UNKNOWN"
        if price_change is not None and oi_change is not None:
            divergence = f"PRICE_{'UP' if _number(price_change) >= 0 else 'DOWN'}_OI_{'UP' if _number(oi_change) >= 0 else 'DOWN'}"
        funding_history = [_number(value) for value in (context.get("funding_history") or [])][-12:]
        oi_history = [_number(value) for value in (context.get("open_interest_history") or [])][-12:]
        funding_trend = "UNAVAILABLE"
        if len(funding_history) >= 3:
            delta = statistics.fmean(funding_history[-2:]) - statistics.fmean(funding_history[:2])
            funding_trend = "RISING" if delta > 0 else "FALLING" if delta < 0 else "STABLE"
        oi_acceleration = "UNAVAILABLE"
        if len(oi_history) >= 4 and all(value > 0 for value in oi_history):
            recent = (oi_history[-1] / oi_history[-2] - 1) * 100
            prior = (oi_history[-2] / oi_history[-3] - 1) * 100
            oi_acceleration = "ACCELERATING" if recent - prior >= .25 else "DECELERATING" if prior - recent >= .25 else "STABLE"
        return {"status": "AVAILABLE", "version": "funding-oi-research-v2",
                "funding_rate": funding, "funding_percentile": context.get("funding_percentile"),
                "open_interest": oi, "open_interest_change_pct": oi_change, "price_oi_state": divergence,
                "funding_trend": funding_trend, "oi_acceleration": oi_acceleration,
                "history_points": {"funding": len(funding_history), "open_interest": len(oi_history)},
                "fabricated": False}

    @staticmethod
    def _strategy_suitability(*, story: Mapping[str, Any], quality: Mapping[str, Any],
                              reversal: Mapping[str, Any], timeframe: str,
                              structure: Mapping[str, Any], liquidity: Mapping[str, Any],
                              momentum: Mapping[str, Any]) -> dict[str, float]:
        market = _number(quality.get("market_quality"), 50)
        state = str(story.get("state") or "UNCERTAIN")
        families = quality.get("family_scores") or {}
        structure_score = _number(families.get("STRUCTURE"), 40)
        liquidity_score = _number(families.get("LIQUIDITY"), 40)
        location_score = _number(families.get("LOCATION"), 40)
        momentum_score = _number(families.get("MOMENTUM"), 40)
        base = {key: market * .35 + 18 for key in (
            "LIQUIDITY_SMC", "TREND_CONTINUATION", "BREAKOUT", "MEAN_REVERSION",
            "PUMP_REVERSAL", "PUMP_CONTINUATION", "LIQUIDITY_SWEEP_REVERSAL",
            "SCALPING_TREND", "SCALPING_BREAKOUT", "SCALPING_MEAN_REVERSION",
            "SCALPING_LIQUIDITY_SWEEP")}
        base["LIQUIDITY_SMC"] += liquidity_score * .25 + location_score * .15
        base["BREAKOUT"] += structure_score * .30 + momentum_score * .10
        base["TREND_CONTINUATION"] += momentum_score * .25 + structure_score * .15
        base["MEAN_REVERSION"] += location_score * .25 + (100 - momentum_score) * .10
        if state == "TREND_CONTINUATION":
            base["TREND_CONTINUATION"] += 25
            base["MEAN_REVERSION"] -= 20
        if state == "BREAKOUT_ATTEMPT":
            base["BREAKOUT"] += 25
        if state in {"RANGE", "LIQUIDITY_SWEEP"}:
            base["MEAN_REVERSION"] += 18
            base["LIQUIDITY_SMC"] += 15
        if "CONFIRMED" in str((reversal.get("pump") or {}).get("state")):
            base["PUMP_REVERSAL"] += 35
            base["PUMP_CONTINUATION"] -= 25
        elif "CONTINUATION_RISK" in str((reversal.get("pump") or {}).get("state")):
            base["PUMP_CONTINUATION"] += 30
            base["PUMP_REVERSAL"] -= 20
        if "CONFIRMED" in str((reversal.get("dump") or {}).get("state")):
            base["LIQUIDITY_SWEEP_REVERSAL"] += 25
        if timeframe not in {"1m", "3m", "5m"}:
            for key in [name for name in base if name.startswith("SCALPING_")]:
                base[key] = min(base[key], 20)
        return {key: _clip(value) for key, value in base.items()}

    @staticmethod
    def _entry_readiness(*, plan: Mapping[str, Any], quality: Mapping[str, Any],
                         microstructure: Mapping[str, Any], momentum: Mapping[str, Any],
                         structure: Mapping[str, Any] | None = None,
                         data_quality: Mapping[str, Any] | None = None) -> dict[str, Any]:
        entry, stop = _number(plan.get("entry")), _number(plan.get("stop"))
        geometry = bool((quality.get("invalidation") or {}).get("valid_geometry"))
        rr = _number(plan.get("rr"))
        location = _number((quality.get("family_scores") or {}).get("LOCATION"), 50)
        momentum_score = _number((quality.get("family_scores") or {}).get("MOMENTUM"), 50)
        raw_micro = (quality.get("family_scores") or {}).get("MICROSTRUCTURE")
        micro_score = _number(raw_micro) if raw_micro is not None else None
        trigger = _number((quality.get("family_scores") or {}).get("STRUCTURE"), 50)
        invalidation = _number((quality.get("family_scores") or {}).get("INVALIDATION"), 0)
        reward_cost = _number((quality.get("family_scores") or {}).get("TARGET_REALISM"), 40)
        execution_cost = _number((quality.get("family_scores") or {}).get("EXECUTION_COST"), 50)
        data_confidence = _number(quality.get("data_confidence") if quality.get("data_confidence") is not None
                                  else (quality.get("confidence") or {}).get("DATA_CONFIDENCE"), 50)
        components = {
            "LOCATION": location,
            "TRIGGER": trigger,
            "MOMENTUM": momentum_score,
            "MICROSTRUCTURE": micro_score,
            "INVALIDATION": invalidation,
            "REWARD_AFTER_COST": _clip(reward_cost * .65 + execution_cost * .35),
        }
        weights = {"LOCATION": .20, "TRIGGER": .18, "MOMENTUM": .17,
                   "MICROSTRUCTURE": .12, "INVALIDATION": .18,
                   "REWARD_AFTER_COST": .15}
        available_weight = sum(weight for key, weight in weights.items() if components[key] is not None)
        score = _clip(sum(float(components[key]) * weight for key, weight in weights.items()
                          if components[key] is not None) / max(available_weight, 1e-12))
        reasons: list[str] = []
        structure_conflict = any(
            item.get("family") == "STRUCTURE" and item.get("severity") in {"HIGH", "CRITICAL"}
            for item in quality.get("contradicting_evidence") or [] if isinstance(item, Mapping))
        if not geometry or entry <= 0 or stop <= 0:
            state, score = "INVALID", min(score, 20)
            reasons.append("INVALID_STOP_OR_ENTRY_GEOMETRY")
        elif (data_quality or {}).get("status") == "INVALID" or data_confidence < 45:
            state, score = "INSUFFICIENT_DATA", min(score, 35)
            reasons.append("MARKET_DATA_INSUFFICIENT")
        elif location < 35:
            state = "CHASING"
            reasons.append("POOR_LOCATION")
        elif structure_conflict:
            state = "WAIT_STRUCTURE"
            score = min(score, 64)
            reasons.append("STRUCTURE_OPPOSES_DIRECTION")
        elif momentum.get("state") in {"EXHAUSTING", "REVERSING"}:
            state = "WAIT_CONFIRMATION"
            reasons.append("MOMENTUM_NOT_CONFIRMED")
        elif trigger < 45 or str((structure or {}).get("break") or "") in {"NO_BREAK", "UNKNOWN"}:
            state = "WAIT_CONFIRMATION"
            reasons.append("STRUCTURE_TRIGGER_MISSING")
        elif score >= 72 and min(location, trigger, momentum_score, invalidation) >= 50:
            state = "READY"
        else:
            state = "WAIT_PULLBACK"
        if microstructure.get("status") != "AVAILABLE":
            reasons.append("MICROSTRUCTURE_NOT_CAPTURED")
        available_components = {key: value for key, value in components.items() if value is not None}
        weakest = sorted(available_components, key=available_components.get)[:2]
        data_state = "COMPLETE" if data_confidence >= 80 else "INCOMPLETE" if data_confidence >= 45 else "INSUFFICIENT"
        return {"version": "entry-readiness-v3", "state": state, "score": round(score, 3),
                "components": components, "weights": weights, "weakest_components": weakest,
                "reason_codes": reasons, "setup_quality": quality.get("setup_quality"),
                "data_confidence": data_confidence, "data_confidence_state": data_state,
                "component_coverage": round(len(available_components) / max(1, len(components)), 4),
                "setup_quality_separate": True,
                "score_is_probability": False, "execution_authority": False}

    @staticmethod
    def _strategy_fusion(scores: Mapping[str, float]) -> dict[str, Any]:
        ranked = sorted(((str(name), _number(score)) for name, score in scores.items()),
                        key=lambda item: (-item[1], item[0]))
        primary = ranked[0] if ranked else ("UNCLASSIFIED", 0.0)
        secondary = ranked[1] if len(ranked) > 1 else ("NONE", 0.0)
        gap = primary[1] - secondary[1]
        tied = [name for name, score in ranked if abs(score - primary[1]) <= .5]
        state = ("LOW_CLASSIFICATION_CONFIDENCE" if primary[1] < 45 else
                 "TIE" if len(tied) > 1 else
                 "HYBRID" if gap < 5 else "PRIMARY")
        return {"version": "strategy-fusion-v2",
                "primary": {"strategy": None if state == "TIE" else primary[0],
                            "suitability": round(primary[1], 3),
                            "classification": state},
                "secondary": {"strategy": secondary[0], "suitability": round(secondary[1], 3),
                              "classification": "SECONDARY"},
                "suitability_gap": round(gap, 3),
                "fusion_state": state, "tied_strategies": tied if state == "TIE" else [],
                "near_tie_threshold": 5.0,
                "scores": dict(scores), "score_is_probability": False,
                "execution_authority": False}

    @staticmethod
    def _market_regime(*, story: Mapping[str, Any], trend: Mapping[str, Any],
                       momentum: Mapping[str, Any], frame: pd.DataFrame) -> dict[str, Any]:
        returns = frame["close"].astype(float).pct_change().dropna().tail(20)
        realized = float(returns.std() * math.sqrt(max(len(returns), 1)) * 100) if len(returns) else 0.0
        story_state = str(story.get("state") or "UNCERTAIN")
        trend_state = str(trend.get("state") or "UNKNOWN")
        if story_state in {"TREND_CONTINUATION", "BREAKOUT_ATTEMPT"}:
            phase = "EXPANSION"
        elif story_state in {"RANGE", "PULLBACK"}:
            phase = "COMPRESSION_OR_ROTATION"
        elif "EXHAUSTION" in story_state:
            phase = "EXHAUSTION"
        else:
            phase = "UNCERTAIN"
        volatility = "HIGH" if realized >= 6 else "LOW" if realized <= 1.5 else "NORMAL"
        return {"version": "market-regime-v2", "phase": phase, "volatility": volatility,
                "realized_volatility_pct": round(realized, 4),
                "trend_state": trend_state, "momentum_state": momentum.get("state"),
                "strategy_implication": {
                    "EXPANSION": "favor continuation after cost and confirmation",
                    "COMPRESSION_OR_ROTATION": "favor selective range or breakout preparation",
                    "EXHAUSTION": "continuation risk is elevated; require reversal evidence",
                    "UNCERTAIN": "reduce confidence and wait for differentiated evidence",
                }[phase], "execution_authority": False}

    @staticmethod
    def _momentum_reacceleration(frame: pd.DataFrame, *, momentum: Mapping[str, Any],
                                 trend: Mapping[str, Any], structure: Mapping[str, Any]) -> dict[str, Any]:
        returns = frame["close"].astype(float).pct_change().dropna()
        if len(returns) < 8:
            return {"version": "momentum-reacceleration-v2", "state": "INSUFFICIENT_HISTORY",
                    "quality": 0.0, "explosive_continuation": False}
        recent = float(returns.tail(3).abs().mean())
        prior = float(returns.iloc[-8:-3].abs().mean())
        acceleration = recent / max(prior, 1e-12)
        directional = abs(float(returns.tail(3).sum())) / max(float(returns.tail(3).abs().sum()), 1e-12)
        structure_confirmed = structure.get("break") == "CLOSE_CONFIRMED_BREAK"
        reaccelerating = acceleration >= 1.25 and directional >= .65
        explosive = reaccelerating and acceleration >= 1.8 and structure_confirmed and trend.get("state") not in {"EXHAUSTED"}
        state = "EXPLOSIVE_CONTINUATION_CANDIDATE" if explosive else "REACCELERATING" if reaccelerating else "NO_REACCELERATION"
        return {"version": "momentum-reacceleration-v2", "state": state,
                "acceleration_ratio": round(acceleration, 4), "directional_efficiency": round(directional, 4),
                "structure_confirmed": structure_confirmed,
                "quality": _clip(min(acceleration, 2.5) / 2.5 * 60 + directional * 40),
                "explosive_continuation": explosive,
                "research_only": True, "execution_authority": False}

    @staticmethod
    def _research_policies(plan: Mapping[str, Any], quality: Mapping[str, Any], timeframe: str) -> dict[str, Any]:
        entry = _number(plan.get("entry"))
        stop = _number(plan.get("stop"))
        risk = abs(entry - stop)
        side = str(plan.get("direction") or plan.get("side") or "LONG").upper()
        rr_candidates = []
        for rr in (1, 1.5, 2, 2.5, 3):
            target = entry + risk * rr if side == "LONG" else entry - risk * rr
            rr_candidates.append({"rr": rr, "target": round(target, 12),
                                  "structurally_validated": False,
                                  "requires_outcome_evaluation": True})
        cost_pct = max(0, _number(os.getenv("RESEARCH_ESTIMATED_EXECUTION_COST_PCT", ".19"), .19))
        risk_pct = risk / max(abs(entry), 1e-12) * 100 if entry else 0
        cost_r = cost_pct / risk_pct if risk_pct else None
        scalp = timeframe in {"1m", "3m", "5m"}
        gross_multiple = risk_pct / max(cost_pct, 1e-12)
        return {
            "rr_candidates": rr_candidates, "planned_rr": plan.get("rr"),
            "philosophy": "EXPECTANCY_AFTER_COST_NOT_MAXIMUM_NOMINAL_RR",
            "entry_policies": ["MARKET_NOW", "CONFIRMATION_CLOSE", "RETEST", "FVG", "ORDER_BLOCK", "LIQUIDITY_RECLAIM"],
            "reentry_policy": {"mode": "SHADOW_RESEARCH_ONLY", "maximum_attempts": 2, "risk_increase_after_loss": False,
                               "requires_new_evidence": True, "new_execution_identity": True,
                               "cumulative_risk_cap_r": 1.5, "cooldown_required": True},
            "estimated_roundtrip_cost_pct": cost_pct, "estimated_cost_r": cost_r,
            "scalping": {"eligible_timeframe": scalp,
                         "gross_move_cost_multiple": round(gross_multiple, 3),
                         "viable_before_outcome_validation": bool(scalp and gross_multiple >= _number(os.getenv("SCALPING_MIN_GROSS_COST_MULTIPLE", "1.5"), 1.5)),
                         "mode": "PAPER_SHADOW_ONLY"},
            "missed_winners_must_be_counted": True, "avoided_losses_must_be_counted": True,
            "automatic_policy_change": False, "quality_at_decision": quality.get("overall_quality"),
        }

    def analyze_timeframe(self, dataframe: Any, *, timeframe: str, side: str,
                          plan: Mapping[str, Any] | None = None,
                          benchmark: pd.DataFrame | None = None,
                          order_books: Iterable[Mapping[str, Any]] | None = None,
                          microstructure_aggregate: Mapping[str, Any] | None = None,
                          funding_oi: Mapping[str, Any] | None = None,
                          previous_story_state: str | None = None) -> dict[str, Any]:
        timeframe = str(timeframe or "unknown").lower()
        frame, data_quality = _prepare_frame(dataframe, timeframe)
        normalized_plan = dict(plan or {})
        normalized_side = str(side or normalized_plan.get("direction") or "NEUTRAL").upper()
        if normalized_side not in {"LONG", "SHORT"}:
            normalized_side = "LONG"
        if frame.empty:
            result = {"version": INTELLIGENCE_VERSION, "mode": "SHADOW_RESEARCH_ONLY",
                      "timeframe": timeframe, "data_quality": data_quality,
                      "economic_authority": False, "future_data_used": False}
            result["snapshot_checksum"] = _checksum(result)
            return result
        level_map = self.levels.analyze(frame, timeframe)
        liquidity = self.liquidity.analyze(level_map)
        structure = self.structure.analyze(frame, level_map)
        zones = self.zones.analyze(frame, normalized_side, level_map)
        momentum, trend = MomentumTrendEngine.analyze(frame)
        reversal = self.reversals.analyze(frame, timeframe, momentum, structure, trend)
        if (microstructure_aggregate and
                microstructure_aggregate.get("version") in {MICROSTRUCTURE_VERSION, "microstructure-v1"} and
                microstructure_aggregate.get("status") == "AVAILABLE" and
                not microstructure_aggregate.get("raw_book_persisted") and
                not contains_raw_order_book(microstructure_aggregate)):
            microstructure = json.loads(_canonical(microstructure_aggregate))
            microstructure["decision_time_source"] = "FRESH_PERSISTED_PUBLIC_AGGREGATE"
        elif order_books is not None:
            microstructure = OrderBookMicrostructureEngine().analyze(order_books)
            microstructure["decision_time_source"] = "BOUNDED_IN_MEMORY_SNAPSHOTS"
        else:
            microstructure = {"version": MICROSTRUCTURE_VERSION,
                              "status": "UNAVAILABLE",
                              "reason_codes": ["NOT_CAPTURED_AT_DECISION_TIME"]}
        relative = self._relative_strength(frame, benchmark)
        embedded_market_context = microstructure.get("funding_open_interest")
        funding_source = (funding_oi if funding_oi is not None else
                          embedded_market_context if isinstance(embedded_market_context, Mapping) else None)
        funding_context = self._funding_oi(funding_source)
        story = MarketStoryEngine.analyze(structure=structure, momentum=momentum, trend=trend,
                                          reversal=reversal, level_map=level_map,
                                          previous_state=previous_story_state)
        quality = self.quality.analyze(side=normalized_side, plan=normalized_plan,
                                       data_quality=data_quality, level_map=level_map,
                                       liquidity=liquidity, structure=structure, zones=zones,
                                       momentum=momentum, trend=trend, story=story,
                                       microstructure=microstructure, relative_strength=relative,
                                       funding_oi=funding_context)
        strategy = self._strategy_suitability(story=story, quality=quality, reversal=reversal,
                                               timeframe=timeframe, structure=structure,
                                               liquidity=liquidity, momentum=momentum)
        strategy_fusion = self._strategy_fusion(strategy)
        strategy_assessments = {
            name: {"suitability_score": score,
                   "supporting_evidence": [story.get("state")],
                   "contradictions": quality.get("contradicting_evidence") or [],
                   "required_confirmation": ["fresh structure and momentum confirmation"],
                   "invalidation": quality.get("invalidation") or {},
                   "target_framework": "STRUCTURE_PLUS_1R_TO_3R",
                   "uncertainty": quality.get("uncertainties") or [],
                   "data_requirements": ["TRUSTWORTHY_DECISION_TIME"],
                   "research_only": True}
            for name, score in strategy.items()
        }
        entry_readiness = self._entry_readiness(plan=normalized_plan, quality=quality,
                                                microstructure=microstructure, momentum=momentum,
                                                structure=structure, data_quality=data_quality)
        regime = self._market_regime(story=story, trend=trend, momentum=momentum, frame=frame)
        reacceleration = self._momentum_reacceleration(
            frame, momentum=momentum, trend=trend, structure=structure,
        )
        research = self._research_policies(normalized_plan, quality, timeframe)
        result = {
            "version": INTELLIGENCE_VERSION, "feature_version": "decision-features-v4",
            "mode": "SHADOW_RESEARCH_ONLY", "timeframe": timeframe, "side": normalized_side,
            "captured_at": datetime.now(timezone.utc).isoformat(), "data_quality": data_quality,
            "market_story": story, "level_map": level_map, "liquidity_map": liquidity,
            "structure_quality": structure, "zone_quality": zones, "momentum": momentum,
            "trend_maturity": trend, "microstructure": microstructure,
            "large_player_inference": {"classification": microstructure.get("absorption_inference", "UNCONFIRMED"),
                                       "actor_identity_claimed": False},
            "reversal_research": reversal, "relative_strength": relative,
            "funding_open_interest": funding_context,
            "signal_quality_v2": quality, "signal_quality_v3": quality,
            "signal_quality_v4": quality,
            "entry_readiness": entry_readiness,
            "strategy_suitability": strategy, "strategy_fusion_v2": strategy_fusion,
            "strategy_assessments": strategy_assessments,
            "market_regime_v2": regime, "momentum_reacceleration": reacceleration,
            "research_policies": research,
            "economic_authority": False, "execution_authority": False,
            "future_data_used": False, "raw_order_book_persisted": False,
        }
        result["snapshot_checksum"] = _checksum(result)
        return _json_safe(result)

    def analyze_hierarchy(self, frames: Mapping[str, Any], *, side: str,
                          plan: Mapping[str, Any] | None = None,
                          benchmark_frames: Mapping[str, pd.DataFrame] | None = None) -> dict[str, Any]:
        results = {}
        for timeframe in ("4h", "1h", "15m"):
            if timeframe in frames:
                results[timeframe] = self.analyze_timeframe(
                    frames[timeframe], timeframe=timeframe, side=side, plan=plan,
                    benchmark=(benchmark_frames or {}).get(timeframe))
        missing = [timeframe for timeframe in ("4h", "1h", "15m") if timeframe not in results]
        directions = {timeframe: (item.get("trend_maturity") or {}).get("direction")
                      for timeframe, item in results.items()}
        expected = "BULLISH" if str(side).upper() == "LONG" else "BEARISH"
        aligned = [timeframe for timeframe, direction in directions.items() if direction == expected]
        opposed = [timeframe for timeframe, direction in directions.items() if direction not in {expected, None, "UNKNOWN"}]
        reversal = any("CONFIRMED" in str(candidate.get("state"))
                       for item in results.values()
                       for candidate in (item.get("reversal_research") or {}).values())
        hierarchy_score = _clip(50 + len(aligned) * 15 - len(opposed) * 15 + (10 if reversal and opposed else 0))
        return {"version": "mtf-hierarchy-v1", "roles": {"4h": "HIGHER_TIMEFRAME_CONTEXT",
                                                            "1h": "SETUP_STRUCTURE",
                                                            "15m": "ENTRY_REFINEMENT"},
                "timeframes": results, "missing_timeframes": missing,
                "directional_alignment": aligned, "intentional_opposition": opposed if reversal else [],
                "contrarian_requires_stronger_reversal_evidence": True,
                "reversal_evidence_present": reversal, "hierarchy_score": hierarchy_score,
                "blind_alignment_required": False, "economic_authority": False}


def concise_market_story(snapshot: Mapping[str, Any]) -> str:
    story = snapshot.get("market_story") or {}
    trend = snapshot.get("trend_maturity") or {}
    structure = snapshot.get("structure_quality") or {}
    momentum = snapshot.get("momentum") or {}
    quality = snapshot.get("signal_quality_v4") or snapshot.get("signal_quality_v3") or snapshot.get("signal_quality_v2") or {}
    facts = [f"Market state is {str(story.get('state') or 'unknown').replace('_', ' ').lower()}.",
             f"Trend is {str(trend.get('state') or 'unknown').lower()} ({str(trend.get('direction') or 'unknown').lower()}).",
             f"Structure is {str(structure.get('break') or 'unknown').replace('_', ' ').lower()}.",
             f"Momentum is {str(momentum.get('state') or 'unknown').lower()}.",
             f"Research quality is {_number(quality.get('overall_quality')):.0f}/100."]
    return " ".join(facts)
