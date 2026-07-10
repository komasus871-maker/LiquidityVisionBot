from __future__ import annotations

import re
from typing import Any

from utils.indicators import ema, rsi, macd
from utils.structure import Structure
from utils.choch import CHOCH
from utils.liquidity import Liquidity
from utils.sweep import Sweep
from utils.order_blocks import OrderBlocks
from utils.breaker_block import BreakerBlock
from utils.mitigation_block import MitigationBlock
from utils.fvg import FVG
from utils.premium_discount import PremiumDiscount
from utils.volume_profile import VolumeProfile
from utils.displacement import Displacement
from utils.atr import ATR


class Analyzer:
    MIN_RR = 1.35
    EDGE_NEUTRAL = 5.0

    @staticmethod
    def _volume_ratio(state: str) -> float:
        match = re.search(r"\(([0-9.]+)x\)", state)
        return float(match.group(1)) if match else 1.0

    @staticmethod
    def _quality(score: float) -> str:
        if score >= 82: return "⭐⭐⭐⭐⭐"
        if score >= 70: return "⭐⭐⭐⭐"
        if score >= 58: return "⭐⭐⭐"
        if score >= 45: return "⭐⭐"
        return "⭐"

    @staticmethod
    def _bias(direction: str, score: float, edge: float) -> str:
        if abs(edge) < Analyzer.EDGE_NEUTRAL:
            return "⚪ Balanced / Two-Sided"
        if direction == "LONG":
            return "🟢 Strong Bullish" if score >= 72 else "🟢 Bullish" if score >= 54 else "🟡 Slightly Bullish"
        return "🔴 Strong Bearish" if score >= 72 else "🔴 Bearish" if score >= 54 else "🟡 Slightly Bearish"

    @staticmethod
    def _aligned(direction: str, text: str) -> bool:
        return (direction == "LONG" and "Bullish" in text) or (direction == "SHORT" and "Bearish" in text)

    def _side_score(self, direction: str, raw: dict[str, Any]) -> tuple[float, list[str], list[str], int]:
        long = direction == "LONG"
        points = 18.0
        positives: list[str] = []
        risks: list[str] = []
        blockers = 0

        def reward(name: str, condition: bool, weight: float):
            nonlocal points
            if condition:
                points += weight
                positives.append(f"✅ {name}")

        def penalize(name: str, condition: bool, weight: float, blocker: bool = False):
            nonlocal points, blockers
            if condition:
                points -= weight
                risks.append(f"{'⛔' if blocker else '⚠️'} {name}")
                blockers += int(blocker)

        trend_ok = self._aligned(direction, raw["trend"])
        structure_ok = self._aligned(direction, raw["structure"])
        bos_ok = self._aligned(direction, raw["bos"])
        choch_ok = self._aligned(direction, raw["choch"])
        sweep_ok = (long and "Sell Side" in raw["sweep"]) or ((not long) and "Buy Side" in raw["sweep"])
        ob_ok = self._aligned(direction, raw["order_block"])
        breaker_ok = self._aligned(direction, raw["breaker"])
        mitigation_ok = self._aligned(direction, raw["mitigation"])
        fvg_ok = self._aligned(direction, raw["fvg"])
        location_ok = (long and "Discount" in raw["premium"]["zone"]) or ((not long) and "Premium" in raw["premium"]["zone"])
        displacement_ok = self._aligned(direction, raw["displacement"]) and "Weak" not in raw["displacement"]
        adverse_displacement = (not self._aligned(direction, raw["displacement"])) and any(
            strength in raw["displacement"] for strength in ("Moderate", "Strong")
        )
        strong_adverse_displacement = (not self._aligned(direction, raw["displacement"])) and "Strong" in raw["displacement"]
        macd_ok = (long and raw["macd_bullish"]) or ((not long) and not raw["macd_bullish"])

        reward("Trend aligned", trend_ok, 11)
        reward("Market structure aligned", structure_ok, 13)
        reward("BOS confirmation", bos_ok, 11)
        reward("CHOCH confirmation", choch_ok, 9)
        reward("Liquidity sweep confirmation", sweep_ok, 10)
        reward("Order Block confirmation", ob_ok, 9)
        reward("Breaker Block confirmation", breaker_ok, 6)
        reward("Mitigation Block confirmation", mitigation_ok, 6)
        reward("Fair Value Gap confirmation", fvg_ok, 8)
        reward("Premium/Discount location aligned", location_ok, 7)
        reward("Displacement confirmation", displacement_ok, 7)
        reward("Momentum aligned", macd_ok, 5)
        reward("Healthy relative volume", raw["volume_ratio"] >= 0.85, 4)

        penalize("Counter-trend context", not trend_ok, 5)
        penalize("Structure conflicts with direction", not structure_ok and "Range" not in raw["structure"], 6, True)
        penalize("Weak displacement", "Weak" in raw["displacement"], 3)
        penalize("Displacement conflicts with direction", adverse_displacement, 7, strong_adverse_displacement)
        penalize(f"Low relative volume ({raw['volume_ratio']}x)", raw["volume_ratio"] < 0.55, 4, True)
        penalize(f"Below-average volume ({raw['volume_ratio']}x)", 0.55 <= raw["volume_ratio"] < 0.85, 2)
        penalize(f"RSI overbought ({raw['rsi']:.1f})", long and raw["rsi"] >= 72, 5, True)
        penalize(f"RSI oversold ({raw['rsi']:.1f})", (not long) and raw["rsi"] <= 28, 5, True)
        penalize("LONG entry is in Premium", long and "Premium" in raw["premium"]["zone"], 5, True)
        penalize("SHORT entry is in Discount", (not long) and "Discount" in raw["premium"]["zone"], 5, True)
        return max(0.0, min(100.0, points)), positives, risks, blockers

    def _execution_metrics(self, direction: str, raw: dict[str, Any], direction_score: float, rr: float, blockers: int, edge: float):
        long = direction == "LONG"
        position = float(raw["premium"].get("premium", 50))
        volume = raw["volume_ratio"]
        rsi_value = raw["rsi"]
        trend_ok = self._aligned(direction, raw["trend"])
        structure_ok = self._aligned(direction, raw["structure"])
        bos_ok = self._aligned(direction, raw["bos"])
        choch_ok = self._aligned(direction, raw["choch"])
        displacement_ok = self._aligned(direction, raw["displacement"])
        strong_adverse_displacement = (not displacement_ok) and "Strong" in raw["displacement"]
        moderate_adverse_displacement = (not displacement_ok) and "Moderate" in raw["displacement"]
        balanced = abs(edge) < self.EDGE_NEUTRAL

        entry_quality = 72.0
        if long:
            if position >= 85: entry_quality -= 38
            elif position >= 70: entry_quality -= 24
            elif position <= 38: entry_quality += 12
        else:
            if position <= 15: entry_quality -= 38
            elif position <= 30: entry_quality -= 24
            elif position >= 62: entry_quality += 12
        if (long and rsi_value >= 72) or ((not long) and rsi_value <= 28): entry_quality -= 14
        if "Weak" in raw["displacement"]: entry_quality -= 10
        if moderate_adverse_displacement: entry_quality -= 12
        if strong_adverse_displacement: entry_quality -= 24
        if volume < 0.55: entry_quality -= 12
        elif volume < 0.85: entry_quality -= 5
        entry_quality = round(max(0, min(100, entry_quality)), 1)

        risk_quality = 76.0
        if rr < 1.35: risk_quality -= 45
        elif rr < 2: risk_quality -= 18
        elif rr >= 3: risk_quality += 8
        risk_quality -= blockers * 8
        if strong_adverse_displacement: risk_quality -= 18
        elif moderate_adverse_displacement: risk_quality -= 8
        if raw["atr"]["atr"] / raw["price"] > 0.03: risk_quality -= 10
        risk_quality = round(max(0, min(100, risk_quality)), 1)

        readiness = round(max(0, min(100, direction_score * .42 + entry_quality * .35 + risk_quality * .23)), 1)
        blocking_entry = (long and position >= 80) or ((not long) and position <= 20)
        if blocking_entry:
            readiness = min(readiness, 54.0)
        if strong_adverse_displacement:
            readiness = min(readiness, 49.0)
        if not trend_ok and not (bos_ok or choch_ok):
            readiness = min(readiness, 59.0)
        if balanced:
            readiness = min(readiness, 56.0)

        low = float(raw["premium"]["low"]); eq = float(raw["premium"]["equilibrium"]); high = float(raw["premium"]["high"])
        atr = float(raw["atr"]["atr"])
        if long:
            zone_low = max(low, eq - atr * 0.9)
            zone_high = min(eq + atr * 0.25, raw["price"])
        else:
            zone_low = max(eq - atr * 0.25, raw["price"])
            zone_high = min(high, eq + atr * 0.9)
        if zone_low > zone_high:
            zone_low, zone_high = sorted((zone_low, zone_high))

        ready_structure = structure_ok and (trend_ok or bos_ok or choch_ok)
        ready_momentum = not strong_adverse_displacement
        ready_edge = abs(edge) >= 10
        can_be_ready = (
            readiness >= 68
            and direction_score >= 58
            and not blocking_entry
            and blockers == 0
            and ready_structure
            and ready_momentum
            and ready_edge
        )

        reversal_evidence = sum((
            int(not trend_ok), int(choch_ok),
            int((long and "Sell Side" in raw["sweep"]) or ((not long) and "Buy Side" in raw["sweep"])),
            int(self._aligned(direction, raw["breaker"])),
            int(self._aligned(direction, raw["order_block"])),
        ))

        if balanced:
            status, category = "🔵 WATCHLIST", "WATCHLIST"
        elif can_be_ready:
            status, category = "🟢 READY", "READY_NOW"
        elif blocking_entry:
            status, category = "🎯 WAIT FOR PULLBACK", "PULLBACK"
        elif direction_score >= 58:
            status, category = "🟡 WAIT FOR TRIGGER", "CONFIRMATION"
        elif reversal_evidence >= 2 and 45 <= direction_score < 58:
            status, category = "🔄 REVERSAL WATCH", "REVERSAL"
        else:
            status, category = "🔵 WATCHLIST", "WATCHLIST"

        return entry_quality, risk_quality, readiness, status, category, zone_low, zone_high

    def analyze(self, df):
        if "confirm" in df.columns:
            confirmed = df[df["confirm"].astype(str) == "1"]
            if len(confirmed) >= 220:
                df = confirmed.copy()

        structure = Structure(df); choch = CHOCH(df); liquidity = Liquidity(df); sweep = Sweep(df)
        order_blocks = OrderBlocks(df); breaker = BreakerBlock(df); mitigation = MitigationBlock(df)
        fvg = FVG(df); premium = PremiumDiscount(df); volume = VolumeProfile(df)
        displacement = Displacement(df); atr = ATR(df)

        close = float(df["close"].iloc[-1])
        ema50 = float(ema(df, 50).iloc[-1]); ema200 = float(ema(df, 200).iloc[-1])
        rsi_value = float(rsi(df).iloc[-1]); macd_line, signal = macd(df)
        macd_now = float(macd_line.iloc[-1]); signal_now = float(signal.iloc[-1])

        raw = {
            "price": close, "trend": "🟢 Bullish" if ema50 > ema200 else "🔴 Bearish",
            "structure": structure.market_structure(), "bos": structure.bos(), "choch": choch.analyze(),
            "liquidity": liquidity.analyze(), "sweep": sweep.analyze(), "order_block": order_blocks.analyze(),
            "breaker": breaker.analyze(), "mitigation": mitigation.analyze(), "fvg": fvg.analyze(),
            "premium": premium.analyze(), "volume": volume.analyze(), "displacement": displacement.analyze(),
            "atr": atr.analyze(), "ema50": ema50, "ema200": ema200, "rsi": rsi_value,
            "macd": "🟢 Bullish" if macd_now > signal_now else "🔴 Bearish", "macd_bullish": macd_now > signal_now,
        }
        raw["volume_ratio"] = self._volume_ratio(raw["volume"])

        long_base, long_pos, long_risks, long_blockers = self._side_score("LONG", raw)
        short_base, short_pos, short_risks, short_blockers = self._side_score("SHORT", raw)
        edge = round(long_base - short_base, 1)
        direction = "LONG" if edge > self.EDGE_NEUTRAL else "SHORT" if edge < -self.EDGE_NEUTRAL else ("LONG" if raw["macd_bullish"] else "SHORT")

        long = direction == "LONG"
        stop = raw["atr"]["long_stop"] if long else raw["atr"]["short_stop"]
        tp1, tp2, tp3 = raw["atr"]["long_tp"] if long else raw["atr"]["short_tp"]
        risk = abs(close - stop)
        rr = round(abs(tp3 - close) / risk, 2) if risk > 0 else 0.0
        rr = min(rr, 12.0)

        score = long_base if long else short_base
        positives = long_pos if long else short_pos
        risks = long_risks if long else short_risks
        blockers = long_blockers if long else short_blockers
        confirmations = len(positives)
        if rr < self.MIN_RR:
            score -= 10; blockers += 1; risks.append(f"⛔ RR below 1:{self.MIN_RR}")
        score = round(max(0.0, min(100.0, score)), 1)

        entry_quality, risk_quality, readiness, execution_status, opportunity_category, zone_low, zone_high = self._execution_metrics(direction, raw, score, rr, blockers, edge)

        side = "BUY" if long else "SELL"; icon = "🟢" if long else "🔴"
        if abs(edge) < self.EDGE_NEUTRAL:
            recommendation = "⚖️ TWO-SIDED / NO CLEAR EDGE"
        elif readiness >= 78 and score >= 76 and execution_status == "🟢 READY":
            recommendation = f"🔥 STRONG {side}"
        elif execution_status == "🟢 READY":
            recommendation = f"{icon} {side}"
        elif execution_status == "🎯 WAIT FOR PULLBACK":
            recommendation = f"🎯 {side} ON PULLBACK"
        elif score >= 55:
            recommendation = f"🟡 CONDITIONAL {side}"
        else:
            recommendation = "📈 BULLISH BIAS" if long else "📉 BEARISH BIAS"

        alternative_direction = "SHORT" if long else "LONG"
        alternative_score = round(short_base if long else long_base, 1)
        alternative_conditions = [
            f"{alternative_direction.title()} BOS/CHOCH",
            "Reaction from opposing premium/discount zone",
            f"{alternative_direction.title()} displacement with volume",
        ]

        triggers = []
        if execution_status == "🎯 WAIT FOR PULLBACK":
            triggers.append("Wait for price to enter the preferred entry zone")
            triggers.append("Require reaction candle or BOS/CHOCH from that zone")
        elif execution_status != "🟢 READY":
            if not any("BOS confirmation" in x or "CHOCH confirmation" in x for x in positives):
                triggers.append("Wait for BOS or CHOCH in the setup direction")
            if "Weak" in raw["displacement"]: triggers.append("Require moderate/strong displacement")
            if raw["volume_ratio"] < 0.85: triggers.append("Prefer confirmed volume above 0.85x")
            if not triggers: triggers.append("Wait for one additional independent confirmation")

        rr_component = min(rr / 4.0, 1.0) * 100
        ranking_score = round(readiness * .45 + score * .3 + rr_component * .15 + min(confirmations / 8, 1) * 10, 2)

        return {
            **raw, "entry": close, "stop": stop, "tp1": tp1, "tp2": tp2, "tp3": tp3, "rr": rr,
            "direction": direction, "primary_scenario": direction, "alternative_scenario": alternative_direction,
            "alternative_score": alternative_score, "alternative_conditions": alternative_conditions,
            "long_score": round(long_base, 1), "short_score": round(short_base, 1), "directional_edge": edge,
            "bull_score": round(long_base, 2), "bear_score": round(short_base, 2),
            "score": score, "direction_score": score, "entry_quality": entry_quality,
            "risk_quality": risk_quality, "execution_readiness": readiness,
            "probability": score, "confidence": score, "confirmations": confirmations,
            "ranking_score": ranking_score, "quality": self._quality(readiness),
            "market_bias": self._bias(direction, score, edge), "recommendation": recommendation,
            "execution_status": execution_status, "opportunity_category": opportunity_category,
            "preferred_entry_low": zone_low, "preferred_entry_high": zone_high,
            "reasons": positives + risks, "triggers": triggers, "blockers": blockers,
        }
