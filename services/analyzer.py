from __future__ import annotations

import re
from typing import Any

from services.trade_plan_integrity import TradePlanIntegrity, InvalidTradePlan
from services.unified_core import unified_pipeline


class Analyzer:
    """Deterministic market and execution analysis.

    v4.8 deliberately separates two questions that used to be mixed together:

    1. Market direction: where the market context currently leans.
    2. Execution quality: whether entering at the current price is sensible.

    Premium/discount location, weak volume and exhaustion therefore reduce entry
    quality and readiness, but they no longer incorrectly erase a valid trend.
    """

    MIN_RR = 1.35
    EDGE_NEUTRAL = 6.0

    @staticmethod
    def _volume_ratio(state: str) -> float:
        match = re.search(r"\(([0-9.]+)x\)", state)
        return float(match.group(1)) if match else 1.0

    @staticmethod
    def _quality(score: float) -> str:
        if score >= 82:
            return "⭐⭐⭐⭐⭐"
        if score >= 70:
            return "⭐⭐⭐⭐"
        if score >= 58:
            return "⭐⭐⭐"
        if score >= 45:
            return "⭐⭐"
        return "⭐"

    @staticmethod
    def _grade(score: float) -> str:
        if score >= 82:
            return "A+"
        if score >= 72:
            return "A"
        if score >= 62:
            return "B"
        if score >= 52:
            return "C"
        if score >= 42:
            return "D"
        return "E"

    @staticmethod
    def _bias(direction: str, score: float, edge: float) -> str:
        if abs(edge) < Analyzer.EDGE_NEUTRAL:
            return "⚪ Balanced / Two-Sided"
        if direction == "LONG":
            if score >= 78:
                return "🟢 Strong Bullish"
            if score >= 58:
                return "🟢 Bullish"
            return "🟡 Slightly Bullish"
        if score >= 78:
            return "🔴 Strong Bearish"
        if score >= 58:
            return "🔴 Bearish"
        return "🟡 Slightly Bearish"

    @staticmethod
    def _aligned(direction: str, text: str) -> bool:
        return (direction == "LONG" and "Bullish" in text) or (
            direction == "SHORT" and "Bearish" in text
        )

    @staticmethod
    def _opposed(direction: str, text: str) -> bool:
        return (direction == "LONG" and "Bearish" in text) or (
            direction == "SHORT" and "Bullish" in text
        )

    @staticmethod
    def _component(label: str, value: float, group: str) -> dict[str, Any]:
        return {"label": label, "value": round(float(value), 1), "group": group}

    def _direction_score(
        self, direction: str, raw: dict[str, Any]
    ) -> tuple[float, list[str], list[str], dict[str, float], list[dict[str, Any]]]:
        """Score directional context only, without judging current entry price."""

        long = direction == "LONG"
        score = 40.0
        positives: list[str] = []
        warnings: list[str] = []
        components: list[dict[str, Any]] = []
        groups = {"Trend": 0.0, "Structure": 0.0, "Liquidity/SMC": 0.0, "Momentum": 0.0}

        def apply(label: str, condition: bool, weight: float, group: str, positive: bool = True):
            nonlocal score
            if not condition:
                return
            score += weight
            groups[group] += weight
            components.append(self._component(label, weight, group))
            if positive and weight > 0:
                positives.append(f"✅ {label}")
            elif weight < 0:
                warnings.append(f"⚠️ {label}")

        trend_ok = self._aligned(direction, raw["trend"])
        trend_bad = self._opposed(direction, raw["trend"])
        structure_ok = self._aligned(direction, raw["structure"])
        structure_bad = self._opposed(direction, raw["structure"])
        bos_ok = self._aligned(direction, raw["bos"])
        bos_bad = self._opposed(direction, raw["bos"])
        choch_ok = self._aligned(direction, raw["choch"])
        choch_bad = self._opposed(direction, raw["choch"])
        sweep_ok = (long and "Sell Side" in raw["sweep"]) or (
            (not long) and "Buy Side" in raw["sweep"]
        )
        sweep_bad = (long and "Buy Side" in raw["sweep"]) or (
            (not long) and "Sell Side" in raw["sweep"]
        )
        ob_ok = self._aligned(direction, raw["order_block"])
        ob_bad = self._opposed(direction, raw["order_block"])
        breaker_ok = self._aligned(direction, raw["breaker"])
        breaker_bad = self._opposed(direction, raw["breaker"])
        mitigation_ok = self._aligned(direction, raw["mitigation"])
        mitigation_bad = self._opposed(direction, raw["mitigation"])
        fvg_ok = self._aligned(direction, raw["fvg"])
        fvg_bad = self._opposed(direction, raw["fvg"])
        macd_ok = (long and raw["macd_bullish"]) or ((not long) and not raw["macd_bullish"])
        displacement_ok = self._aligned(direction, raw["displacement"])
        displacement_bad = self._opposed(direction, raw["displacement"])
        displacement_meaningful = any(x in raw["displacement"] for x in ("Moderate", "Strong"))

        apply("Trend aligned", trend_ok, 18, "Trend")
        apply("Trend conflicts", trend_bad, -18, "Trend", positive=False)

        apply("Market structure aligned", structure_ok, 15, "Structure")
        apply("Structure conflicts", structure_bad, -13, "Structure", positive=False)
        apply("BOS confirmation", bos_ok, 12, "Structure")
        apply("Opposing BOS", bos_bad, -11, "Structure", positive=False)
        apply("CHOCH confirmation", choch_ok, 10, "Structure")
        apply("Opposing CHOCH", choch_bad, -9, "Structure", positive=False)

        apply("Liquidity sweep confirmation", sweep_ok, 8, "Liquidity/SMC")
        apply("Opposing liquidity sweep", sweep_bad, -6, "Liquidity/SMC", positive=False)
        apply("Order Block confirmation", ob_ok, 7, "Liquidity/SMC")
        apply("Opposing Order Block", ob_bad, -5, "Liquidity/SMC", positive=False)
        apply("Breaker Block confirmation", breaker_ok, 6, "Liquidity/SMC")
        apply("Opposing Breaker Block", breaker_bad, -5, "Liquidity/SMC", positive=False)
        apply("Mitigation Block confirmation", mitigation_ok, 5, "Liquidity/SMC")
        apply("Opposing Mitigation Block", mitigation_bad, -4, "Liquidity/SMC", positive=False)
        apply("Fair Value Gap confirmation", fvg_ok, 6, "Liquidity/SMC")
        apply("Opposing Fair Value Gap", fvg_bad, -5, "Liquidity/SMC", positive=False)

        apply("Momentum aligned", macd_ok, 6, "Momentum")
        apply("Momentum conflicts", not macd_ok, -5, "Momentum", positive=False)
        apply(
            "Displacement confirmation",
            displacement_ok and displacement_meaningful,
            8,
            "Momentum",
        )
        apply(
            "Displacement conflicts with direction",
            displacement_bad and displacement_meaningful,
            -8,
            "Momentum",
            positive=False,
        )

        # RSI is intentionally a light directional input. Extremes are handled
        # by the execution/exhaustion model instead of destroying market bias.
        rsi_value = float(raw["rsi"])
        apply("RSI supports direction", long and 52 <= rsi_value <= 68, 3, "Momentum")
        apply("RSI supports direction", (not long) and 32 <= rsi_value <= 48, 3, "Momentum")

        return (
            round(max(0.0, min(100.0, score)), 1),
            positives,
            warnings,
            {key: round(value, 1) for key, value in groups.items()},
            components,
        )

    def _execution_metrics(
        self,
        direction: str,
        raw: dict[str, Any],
        direction_score: float,
        rr: float,
        edge: float,
    ) -> dict[str, Any]:
        long = direction == "LONG"
        position = float(raw["premium"].get("premium", 50))
        volume = float(raw["volume_ratio"])
        rsi_value = float(raw["rsi"])
        trend_ok = self._aligned(direction, raw["trend"])
        structure_ok = self._aligned(direction, raw["structure"])
        bos_ok = self._aligned(direction, raw["bos"])
        choch_ok = self._aligned(direction, raw["choch"])
        ob_ok = self._aligned(direction, raw["order_block"])
        breaker_ok = self._aligned(direction, raw["breaker"])
        fvg_ok = self._aligned(direction, raw["fvg"])
        displacement_ok = self._aligned(direction, raw["displacement"])
        displacement_bad = self._opposed(direction, raw["displacement"])
        strong_adverse = displacement_bad and "Strong" in raw["displacement"]
        moderate_adverse = displacement_bad and "Moderate" in raw["displacement"]
        balanced = abs(edge) < self.EDGE_NEUTRAL

        entry_quality = 68.0
        entry_components: list[dict[str, Any]] = []
        blockers: list[str] = []
        warnings: list[str] = []

        def entry_adjust(label: str, value: float, hard: bool = False):
            nonlocal entry_quality
            entry_quality += value
            entry_components.append(self._component(label, value, "Entry"))
            if value < 0:
                (blockers if hard else warnings).append(f"{'⛔' if hard else '⚠️'} {label}")

        regime = raw.get("market_regime") or {}
        regime_code = str(regime.get("code") or "UNKNOWN")
        regime_direction = str(regime.get("direction") or "NEUTRAL")
        regime_aligned = regime_direction == direction

        if regime_code == "TRENDING" and regime_aligned:
            entry_adjust("Market regime supports trend execution", 8)
        elif regime_code == "TRENDING" and regime_direction not in ("NEUTRAL", direction):
            entry_adjust("Market regime conflicts with direction", -20, True)
        elif regime_code == "RANGING":
            entry_adjust("Ranging/choppy regime is hostile to trend entries", -18, True)
        elif regime_code == "COMPRESSION":
            entry_adjust("Compressed regime requires confirmed breakout", -14, True)
        elif regime_code == "VOLATILE_EXPANSION":
            entry_adjust("Volatile expansion makes current entry late", -16, True)
        elif regime_code in ("TRANSITION", "UNKNOWN"):
            entry_adjust("Transitional regime requires extra confirmation", -8)

        location_aligned = (long and position <= 38) or ((not long) and position >= 62)
        location_bad = (long and position >= 70) or ((not long) and position <= 30)
        location_extreme = (long and position >= 85) or ((not long) and position <= 15)

        if location_aligned:
            entry_adjust("Price located in favorable dealing range", 14)
        if location_bad:
            entry_adjust("LONG entry is in Premium" if long else "SHORT entry is in Discount", -20, True)
        if location_extreme:
            entry_adjust("Entry is near an extreme of the dealing range", -12, True)

        if volume >= 1.25:
            entry_adjust("Elevated confirmed volume", 8)
        elif volume >= 0.85:
            entry_adjust("Healthy confirmed volume", 4)
        elif volume < 0.55:
            entry_adjust(f"Low relative volume ({volume}x)", -14, True)
        else:
            entry_adjust(f"Below-average volume ({volume}x)", -6)

        if displacement_ok and any(x in raw["displacement"] for x in ("Moderate", "Strong")):
            entry_adjust("Displacement supports execution", 8)
        elif strong_adverse:
            entry_adjust("Strong displacement conflicts with direction", -22, True)
        elif moderate_adverse:
            entry_adjust("Displacement conflicts with direction", -12, True)
        elif "Weak" in raw["displacement"]:
            entry_adjust("Weak displacement", -6)

        if bos_ok or choch_ok:
            entry_adjust("Structure trigger is present", 8)
        elif not structure_ok:
            entry_adjust("No aligned structural trigger", -8)

        if ob_ok or breaker_ok or fvg_ok:
            entry_adjust("Active execution zone is present", 5)

        # Exhaustion: continuation may remain directionally valid, but entering
        # after a large impulse at an extreme is usually poor execution.
        continuation_exhaustion = (
            long
            and position >= 75
            and rsi_value >= 68
            and self._aligned("LONG", raw["displacement"])
            and "Strong" in raw["displacement"]
        ) or (
            (not long)
            and position <= 25
            and rsi_value <= 32
            and self._aligned("SHORT", raw["displacement"])
            and "Strong" in raw["displacement"]
        )
        impulse_exhaustion = (
            long and position >= 78 and rsi_value >= 70
        ) or ((not long) and position <= 22 and rsi_value <= 30)
        if continuation_exhaustion:
            entry_adjust("Continuation exhaustion risk", -18, True)
        elif impulse_exhaustion:
            entry_adjust("Market is stretched near an execution extreme", -10, True)

        entry_quality = round(max(0, min(100, entry_quality)), 1)

        risk_quality = 72.0
        risk_components: list[dict[str, Any]] = []

        def risk_adjust(label: str, value: float):
            nonlocal risk_quality
            risk_quality += value
            risk_components.append(self._component(label, value, "Risk"))

        if rr < self.MIN_RR:
            risk_adjust(f"RR below 1:{self.MIN_RR}", -42)
            blockers.append(f"⛔ RR below 1:{self.MIN_RR}")
        elif rr < 2:
            risk_adjust("RR below 1:2", -16)
        elif rr >= 3:
            risk_adjust("Attractive planned RR", 10)

        atr_ratio = float(raw["atr"]["atr"]) / max(float(raw["price"]), 1e-12)
        if atr_ratio > 0.04:
            risk_adjust("Very high ATR volatility", -14)
            warnings.append("⚠️ Very high ATR volatility")
        elif atr_ratio > 0.025:
            risk_adjust("Elevated ATR volatility", -8)
            warnings.append("⚠️ Elevated ATR volatility")

        risk_quality -= len(blockers) * 5
        risk_components.append(self._component("Hard blocker penalty", -len(blockers) * 5, "Risk"))
        risk_quality = round(max(0, min(100, risk_quality)), 1)

        readiness = round(
            max(0, min(100, direction_score * 0.40 + entry_quality * 0.38 + risk_quality * 0.22)),
            1,
        )

        if blockers:
            readiness = min(readiness, 54.0)
        if strong_adverse:
            readiness = min(readiness, 46.0)
        if not trend_ok and not (bos_ok or choch_ok):
            readiness = min(readiness, 56.0)
        if balanced:
            readiness = min(readiness, 52.0)
        if regime_code == "RANGING":
            readiness = min(readiness, 44.0)
        elif regime_code == "COMPRESSION":
            readiness = min(readiness, 50.0)
        elif regime_code == "VOLATILE_EXPANSION":
            readiness = min(readiness, 52.0)
        elif regime_code in ("TRANSITION", "UNKNOWN"):
            readiness = min(readiness, 62.0)
        elif regime_code == "TRENDING" and not regime_aligned:
            readiness = min(readiness, 42.0)

        low = float(raw["premium"]["low"])
        eq = float(raw["premium"]["equilibrium"])
        high = float(raw["premium"]["high"])
        atr = float(raw["atr"]["atr"])
        if long:
            zone_low = max(low, eq - atr * 0.9)
            zone_high = min(eq + atr * 0.25, raw["price"])
        else:
            zone_low = max(eq - atr * 0.25, raw["price"])
            zone_high = min(high, eq + atr * 0.9)
        if zone_low > zone_high:
            zone_low, zone_high = sorted((zone_low, zone_high))

        can_be_ready = (
            readiness >= 70
            and direction_score >= 60
            and abs(edge) >= 12
            and not blockers
            and (structure_ok or bos_ok or choch_ok)
            and not strong_adverse
            and regime_code == "TRENDING"
            and regime_aligned
            and bool(regime.get("allows_trend_entry"))
        )
        pullback_valid = (
            direction_score >= 56
            and abs(edge) >= 12
            and risk_quality >= 55
            and location_bad
            and not balanced
        )
        reversal_evidence = sum(
            (
                int(not trend_ok),
                int(choch_ok),
                int(bos_ok and not trend_ok),
                int((long and "Sell Side Sweep" in raw["liquidity"]) or ((not long) and "Buy Side Sweep" in raw["liquidity"])),
                int(displacement_ok and "Strong" in raw["displacement"]),
                int(location_aligned and (ob_ok or breaker_ok or fvg_ok)),
            )
        )
        reversal_valid = reversal_evidence >= 2 and 50 <= direction_score < 62 and abs(edge) >= 10

        if balanced:
            status, category = "🔵 WATCHLIST", "WATCHLIST"
        elif can_be_ready:
            status, category = "🟢 READY", "READY_NOW"
        elif pullback_valid:
            status, category = "🎯 WAIT FOR PULLBACK", "PULLBACK"
        elif direction_score >= 60 and abs(edge) >= 10:
            status, category = "🟡 WAIT FOR TRIGGER", "CONFIRMATION"
        elif reversal_valid:
            status, category = "🔄 REVERSAL WATCH", "REVERSAL"
        else:
            status, category = "🔵 WATCHLIST", "WATCHLIST"

        # Regime is the final execution gate. A directional score must not turn
        # a choppy/compressed market into an executable trend setup.
        if regime_code == "RANGING":
            status, category = "🔵 WATCHLIST", "REGIME_BLOCKED"
        elif regime_code == "COMPRESSION":
            status, category = "🟡 WAIT FOR TRIGGER", "BREAKOUT_WATCH"
        elif regime_code == "VOLATILE_EXPANSION":
            status, category = "🎯 WAIT FOR PULLBACK", "VOLATILITY_PULLBACK"
        elif regime_code in ("TRANSITION", "UNKNOWN") and status in ("🟢 READY", "🎯 WAIT FOR PULLBACK"):
            status, category = "🟡 WAIT FOR TRIGGER", "REGIME_CONFIRMATION"
        elif regime_code == "TRENDING" and not regime_aligned:
            status, category = "🔵 WATCHLIST", "REGIME_CONFLICT"

        if status == "🟢 READY":
            execution_bias = f"{direction} NOW"
        elif status == "🎯 WAIT FOR PULLBACK":
            execution_bias = f"{direction} ON PULLBACK"
        elif status == "🟡 WAIT FOR TRIGGER":
            execution_bias = f"{direction} AFTER CONFIRMATION"
        elif status == "🔄 REVERSAL WATCH":
            execution_bias = f"{direction} REVERSAL WATCH"
        else:
            execution_bias = "NEUTRAL / OBSERVE"

        return {
            "entry_quality": entry_quality,
            "risk_quality": risk_quality,
            "readiness": readiness,
            "status": status,
            "category": category,
            "zone_low": zone_low,
            "zone_high": zone_high,
            "execution_bias": execution_bias,
            "entry_components": entry_components,
            "risk_components": risk_components,
            "blockers": blockers,
            "warnings": warnings,
            "exhaustion": continuation_exhaustion or impulse_exhaustion,
            "regime_code": regime_code,
            "regime_aligned": regime_aligned,
        }

    @staticmethod
    def _rank_components(components: list[dict[str, Any]], positive: bool, limit: int = 4) -> list[dict[str, Any]]:
        filtered = [x for x in components if (x["value"] > 0 if positive else x["value"] < 0)]
        return sorted(filtered, key=lambda x: abs(x["value"]), reverse=True)[:limit]

    def _verdict(self, data: dict[str, Any]) -> str:
        status = data["execution_status"]
        direction = data["direction"]
        regime = data.get("market_regime") or {}
        regime_code = regime.get("code")
        if status == "🟢 READY":
            return f"✅ {direction} setup is executable in a confirmed trending regime. Respect the stop and position size."
        if regime_code == "RANGING":
            return "🟡 Directional evidence exists, but the market is ranging/choppy. Avoid trend execution until price leaves the range."
        if regime_code == "COMPRESSION":
            return "🟣 Volatility is compressed. Wait for a confirmed breakout and retest before execution."
        if regime_code == "VOLATILE_EXPANSION":
            return "🟠 Direction may be valid, but volatility expansion makes the current entry late and fragile. Wait for normalization or a pullback."
        if regime_code in ("TRANSITION", "UNKNOWN"):
            return f"⚪ Market context leans {direction}, but the regime is transitional. Require additional confirmation before risking capital."
        if status == "🎯 WAIT FOR PULLBACK":
            return f"🎯 Direction favors {direction}, but the current price is inefficient. Wait for the preferred entry zone."
        if status == "🟡 WAIT FOR TRIGGER":
            return f"🔔 Direction favors {direction}, but execution still needs structural or momentum confirmation."
        if status == "🔄 REVERSAL WATCH":
            return f"🔄 Potential {direction} reversal is developing, but it is not confirmed yet."
        if abs(float(data["directional_edge"])) < self.EDGE_NEUTRAL:
            return "⚪ No clear directional edge. Preserve capital and wait for the market to resolve."
        return f"👁 Market context leans {direction}, but this is an observation—not a trade."

    def analyze(self, df, *, symbol=None, timeframe=None, source="analyzer", use_cache=True):
        """Analyze through the v7.6 shared pipeline while preserving legacy output."""
        pipeline_result = unified_pipeline.execute(
            df,
            symbol=symbol,
            timeframe=timeframe,
            source=source,
            use_cache=use_cache,
        )
        context = pipeline_result.context
        raw = dict(context.raw)
        close = float(raw["price"])
        raw["volume_ratio"] = self._volume_ratio(raw["volume"])

        long_score, long_pos, long_warn, long_groups, long_components = self._direction_score("LONG", raw)
        short_score, short_pos, short_warn, short_groups, short_components = self._direction_score("SHORT", raw)
        edge = round(long_score - short_score, 1)

        if edge > self.EDGE_NEUTRAL:
            direction = "LONG"
        elif edge < -self.EDGE_NEUTRAL:
            direction = "SHORT"
        else:
            direction = "LONG" if long_score >= short_score else "SHORT"

        long = direction == "LONG"
        stop = raw["atr"]["long_stop"] if long else raw["atr"]["short_stop"]
        tp1, tp2, tp3 = raw["atr"]["long_tp"] if long else raw["atr"]["short_tp"]
        risk = abs(close - stop)
        rr = round(abs(tp3 - close) / risk, 2) if risk > 0 else 0.0
        rr = min(rr, 12.0)

        direction_score = long_score if long else short_score
        direction_positives = long_pos if long else short_pos
        direction_warnings = long_warn if long else short_warn
        direction_groups = long_groups if long else short_groups
        direction_components = long_components if long else short_components

        execution = self._execution_metrics(direction, raw, direction_score, rr, edge)
        reasons = direction_positives + direction_warnings + execution["warnings"] + execution["blockers"]
        blockers_count = len(execution["blockers"])
        confirmations = len(direction_positives)

        side = "BUY" if long else "SELL"
        icon = "🟢" if long else "🔴"
        if abs(edge) < self.EDGE_NEUTRAL:
            recommendation = "⚖️ TWO-SIDED / NO CLEAR EDGE"
        elif execution["status"] == "🟢 READY" and execution["readiness"] >= 80 and direction_score >= 78:
            recommendation = f"🔥 STRONG {side}"
        elif execution["status"] == "🟢 READY":
            recommendation = f"{icon} {side}"
        elif execution["status"] == "🎯 WAIT FOR PULLBACK":
            recommendation = f"🎯 {side} ON PULLBACK"
        elif execution["status"] == "🟡 WAIT FOR TRIGGER":
            recommendation = f"🟡 CONDITIONAL {side}"
        else:
            recommendation = "📈 BULLISH BIAS" if long else "📉 BEARISH BIAS"

        alternative_direction = "SHORT" if long else "LONG"
        alternative_score = round(short_score if long else long_score, 1)
        alternative_conditions = [
            f"{alternative_direction.title()} BOS/CHOCH",
            "Reaction from opposing premium/discount zone",
            f"{alternative_direction.title()} displacement with volume",
        ]

        triggers: list[str] = []
        if execution["status"] == "🎯 WAIT FOR PULLBACK":
            triggers.extend(
                [
                    "Wait for price to enter the preferred entry zone",
                    "Require a reaction candle or BOS/CHOCH from that zone",
                ]
            )
        elif execution["status"] != "🟢 READY":
            if not any("BOS confirmation" in x or "CHOCH confirmation" in x for x in direction_positives):
                triggers.append("Wait for BOS or CHOCH in the setup direction")
            if "Weak" in raw["displacement"] or self._opposed(direction, raw["displacement"]):
                triggers.append("Require aligned moderate/strong displacement")
            if raw["volume_ratio"] < 0.85:
                triggers.append("Prefer confirmed volume above 0.85x")
            if execution["exhaustion"]:
                triggers.append("Wait for exhaustion to reset before continuation")
            regime_code = str(raw.get("market_regime", {}).get("code") or "UNKNOWN")
            if regime_code == "RANGING":
                triggers.append("Wait for the market to leave the choppy range")
            elif regime_code == "COMPRESSION":
                triggers.append("Require a confirmed breakout and retest")
            elif regime_code == "VOLATILE_EXPANSION":
                triggers.append("Wait for volatility to normalize or for a deep pullback")
            elif regime_code in ("TRANSITION", "UNKNOWN"):
                triggers.append("Wait for the market regime to confirm a trend")
            if not triggers:
                triggers.append("Wait for one additional independent confirmation")

        rr_component = min(rr / 4.0, 1.0) * 100
        regime_score = 100.0 if execution.get("regime_code") == "TRENDING" and execution.get("regime_aligned") else 35.0
        ranking_score = round(
            execution["readiness"] * 0.40
            + direction_score * 0.27
            + rr_component * 0.13
            + min(confirmations / 8, 1) * 10
            + regime_score * 0.10,
            2,
        )

        all_components = direction_components + execution["entry_components"] + execution["risk_components"]
        strongest_drivers = self._rank_components(all_components, positive=True)
        biggest_blockers = self._rank_components(all_components, positive=False)

        data = {
            **raw,
            "entry": close,
            "stop": stop,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "rr": rr,
            "direction": direction,
            "primary_scenario": direction,
            "alternative_scenario": alternative_direction,
            "alternative_score": alternative_score,
            "alternative_conditions": alternative_conditions,
            "long_score": round(long_score, 1),
            "short_score": round(short_score, 1),
            "directional_edge": edge,
            "bull_score": round(long_score, 2),
            "bear_score": round(short_score, 2),
            "score": round(direction_score, 1),
            "direction_score": round(direction_score, 1),
            "entry_quality": execution["entry_quality"],
            "risk_quality": execution["risk_quality"],
            "execution_readiness": execution["readiness"],
            "probability": round(direction_score, 1),
            "confidence": round(direction_score, 1),
            "confirmations": confirmations,
            "ranking_score": ranking_score,
            "quality": self._quality(execution["readiness"]),
            "ai_grade": self._grade(execution["readiness"]),
            "market_bias": self._bias(direction, direction_score, edge),
            "execution_bias": execution["execution_bias"],
            "recommendation": recommendation,
            "execution_status": execution["status"],
            "opportunity_category": execution["category"],
            "preferred_entry_low": execution["zone_low"],
            "preferred_entry_high": execution["zone_high"],
            "reasons": reasons,
            "triggers": triggers,
            "blockers": blockers_count,
            "direction_breakdown": direction_groups,
            "score_components": all_components,
            "strongest_drivers": strongest_drivers,
            "biggest_blockers": biggest_blockers,
            "exhaustion_risk": execution["exhaustion"],
        }
        # Final setup quality must reflect execution constraints, not merely direction.
        data["score"] = round(execution["readiness"], 1)
        data["setup_score"] = round(execution["readiness"], 1)
        data["directional_conviction"] = round(direction_score, 1)
        data["confidence"] = round(direction_score, 1)
        data["probability"] = None
        try:
            TradePlanIntegrity.apply(data)
        except InvalidTradePlan as exc:
            data["plan_valid"] = False
            data["plan_error"] = str(exc)
            data["execution_status"] = "⛔ PLAN INVALID"
            data["recommendation"] = "NO TRADE"
            data["execution_readiness"] = 0.0
            data["score"] = 0.0
            data["setup_score"] = 0.0
            data.setdefault("reasons", []).append(f"⛔ Invalid trade geometry: {exc}")
        context.decision.update(data)
        data["analysis_context"] = context.snapshot()
        data["trade_dna_foundation"] = dict(context.trade_dna)
        data["final_verdict"] = self._verdict(data)
        context.output.update(data)
        return data
