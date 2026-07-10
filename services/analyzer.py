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
    def _recommendation(direction: str, score: float, rr: float, confirmations: int, blockers: int) -> tuple[str, str]:
        side = "BUY" if direction == "LONG" else "SELL"
        icon = "🟢" if direction == "LONG" else "🔴"
        if score >= 79 and rr >= 2.2 and confirmations >= 6 and blockers == 0:
            return f"🔥 STRONG {side}", "🟢 READY"
        if score >= 66 and rr >= 1.65 and confirmations >= 5 and blockers <= 1:
            return f"{icon} {side}", "🟢 READY"
        if score >= 53 and rr >= Analyzer.MIN_RR and confirmations >= 3:
            return f"🟡 CONDITIONAL {side}", "🟡 WAIT FOR TRIGGER"
        if score >= 39:
            return ("📈 BULLISH BIAS" if direction == "LONG" else "📉 BEARISH BIAS"), "🔵 WATCHLIST"
        return "⚪ NEUTRAL / NO EDGE", "⚪ OBSERVE"

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
                blockers += 1 if blocker else 0

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
        penalize(f"Low relative volume ({raw['volume_ratio']}x)", raw["volume_ratio"] < 0.55, 4, True)
        penalize(f"Below-average volume ({raw['volume_ratio']}x)", 0.55 <= raw["volume_ratio"] < 0.85, 2)
        penalize(f"RSI overbought ({raw['rsi']:.1f})", long and raw["rsi"] >= 72, 5, True)
        penalize(f"RSI oversold ({raw['rsi']:.1f})", (not long) and raw["rsi"] <= 28, 5, True)
        penalize("LONG entry is in Premium", long and "Premium" in raw["premium"]["zone"], 5, True)
        penalize("SHORT entry is in Discount", (not long) and "Discount" in raw["premium"]["zone"], 5, True)

        return max(0.0, min(100.0, points)), positives, risks, blockers

    def analyze(self, df):
        # Use confirmed candles when provider exposes confirmation state.
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
            "price": close,
            "trend": "🟢 Bullish" if ema50 > ema200 else "🔴 Bearish",
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
        direction = "LONG" if edge > 0 else "SHORT" if edge < 0 else ("LONG" if raw["macd_bullish"] else "SHORT")

        long = direction == "LONG"
        stop = raw["atr"]["long_stop"] if long else raw["atr"]["short_stop"]
        tp1, tp2, tp3 = raw["atr"]["long_tp"] if long else raw["atr"]["short_tp"]
        risk = abs(close - stop)
        rr = round(abs(tp3 - close) / risk, 2) if risk > 0 else 0.0
        rr = min(rr, 12.0)  # cap display/ranking outliers; geometry remains in levels

        score = long_base if long else short_base
        positives = long_pos if long else short_pos
        risks = long_risks if long else short_risks
        blockers = long_blockers if long else short_blockers
        confirmations = len(positives)
        if rr < self.MIN_RR:
            score -= 10; blockers += 1; risks.append(f"⛔ RR below 1:{self.MIN_RR}")
        score = round(max(0.0, min(100.0, score)), 1)

        recommendation, execution_status = self._recommendation(direction, score, rr, confirmations, blockers)
        if abs(edge) < self.EDGE_NEUTRAL and score < 58:
            recommendation, execution_status = "⚖️ TWO-SIDED / NO CLEAR EDGE", "🔵 WATCHLIST"

        alternative_direction = "SHORT" if direction == "LONG" else "LONG"
        alternative_score = round(short_base if direction == "LONG" else long_base, 1)
        alternative_conditions = []
        if alternative_direction == "SHORT":
            alternative_conditions = ["Bearish BOS/CHOCH", "Rejection from premium or bearish imbalance", "Bearish displacement with volume"]
        else:
            alternative_conditions = ["Bullish BOS/CHOCH", "Reaction from discount or bullish imbalance", "Bullish displacement with volume"]

        triggers = []
        if execution_status != "🟢 READY":
            if not any("BOS confirmation" in x or "CHOCH confirmation" in x for x in positives):
                triggers.append("Wait for BOS or CHOCH in the setup direction")
            if "Weak" in raw["displacement"]:
                triggers.append("Require a moderate/strong displacement candle")
            if raw["volume_ratio"] < 0.85:
                triggers.append("Prefer confirmed volume above 0.85x")
            if long and "Premium" in raw["premium"]["zone"]:
                triggers.append("Prefer a pullback toward equilibrium/discount")
            if (not long) and "Discount" in raw["premium"]["zone"]:
                triggers.append("Prefer a retracement toward equilibrium/premium")
            if not triggers:
                triggers.append("Wait for one additional independent confirmation")

        rr_component = min(rr / 4.0, 1.0) * 100
        edge_component = min(abs(edge) / 30.0, 1.0) * 100
        ranking_score = round(score * 0.50 + rr_component * 0.18 + min(confirmations / 8, 1) * 100 * 0.18 + edge_component * 0.09 + (5 if execution_status == "🟢 READY" else 0), 2)

        return {
            **raw,
            "entry": close, "stop": stop, "tp1": tp1, "tp2": tp2, "tp3": tp3, "rr": rr,
            "direction": direction, "primary_scenario": direction, "alternative_scenario": alternative_direction,
            "alternative_score": alternative_score, "alternative_conditions": alternative_conditions,
            "long_score": round(long_base, 1), "short_score": round(short_base, 1), "directional_edge": edge,
            "bull_score": round(long_base, 2), "bear_score": round(short_base, 2),
            "score": score, "probability": score, "confidence": score, "confirmations": confirmations,
            "ranking_score": ranking_score, "quality": self._quality(score),
            "market_bias": self._bias(direction, score, edge), "recommendation": recommendation,
            "execution_status": execution_status, "reasons": positives + risks, "triggers": triggers,
            "blockers": blockers,
        }
