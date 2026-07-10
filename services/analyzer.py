import re

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

    @staticmethod
    def _volume_ratio(state: str) -> float:
        m = re.search(r"\(([0-9.]+)x\)", state)
        return float(m.group(1)) if m else 1.0

    @staticmethod
    def _quality(score: float) -> str:
        return "⭐⭐⭐⭐⭐" if score >= 82 else "⭐⭐⭐⭐" if score >= 70 else "⭐⭐⭐" if score >= 58 else "⭐⭐" if score >= 45 else "⭐"

    @staticmethod
    def _bias(direction: str, score: float) -> str:
        if direction == "LONG":
            return "🟢 Strong Bullish" if score >= 72 else "🟢 Bullish" if score >= 54 else "🟡 Slightly Bullish"
        return "🔴 Strong Bearish" if score >= 72 else "🔴 Bearish" if score >= 54 else "🟡 Slightly Bearish"

    @staticmethod
    def _recommendation(direction: str, score: float, rr: float, confirmations: int, blockers: int) -> tuple[str, str]:
        side = "BUY" if direction == "LONG" else "SELL"
        if score >= 78 and rr >= 2.2 and confirmations >= 6 and blockers == 0:
            return (f"🔥 STRONG {side}", "🟢 READY")
        if score >= 66 and rr >= 1.7 and confirmations >= 5 and blockers <= 1:
            return (f"{'🟢' if direction == 'LONG' else '🔴'} {side}", "🟢 READY")
        if score >= 55 and rr >= Analyzer.MIN_RR and confirmations >= 4:
            return (f"🟡 CONDITIONAL {side}", "🟡 WAIT FOR TRIGGER")
        if score >= 44:
            return (f"📈 BULLISH BIAS" if direction == "LONG" else "📉 BEARISH BIAS", "🔵 WATCHLIST")
        return ("⚪ NEUTRAL / NO EDGE", "⚪ OBSERVE")

    def analyze(self, df):
        structure = Structure(df); choch = CHOCH(df); liquidity = Liquidity(df); sweep = Sweep(df)
        order_blocks = OrderBlocks(df); breaker = BreakerBlock(df); mitigation = MitigationBlock(df)
        fvg = FVG(df); premium = PremiumDiscount(df); volume = VolumeProfile(df)
        displacement = Displacement(df); atr = ATR(df)

        close = float(df["close"].iloc[-1])
        ema50 = float(ema(df, 50).iloc[-1]); ema200 = float(ema(df, 200).iloc[-1])
        rsi_value = float(rsi(df).iloc[-1]); macd_line, signal = macd(df)
        macd_now = float(macd_line.iloc[-1]); signal_now = float(signal.iloc[-1])

        trend = "🟢 Bullish" if ema50 > ema200 else "🔴 Bearish"
        macd_state = "🟢 Bullish" if macd_now > signal_now else "🔴 Bearish"
        structure_state = structure.market_structure(); bos = structure.bos(); choch_state = choch.analyze()
        liquidity_state = liquidity.analyze(); sweep_state = sweep.analyze(); order_block = order_blocks.analyze()
        breaker_block = breaker.analyze(); mitigation_block = mitigation.analyze(); fvg_state = fvg.analyze()
        premium_state = premium.analyze(); volume_state = volume.analyze(); displacement_state = displacement.analyze()
        atr_state = atr.analyze(); volume_ratio = self._volume_ratio(volume_state)

        bull = bear = 0.0
        def add(text, weight):
            nonlocal bull, bear
            if "Bullish" in text: bull += weight
            elif "Bearish" in text: bear += weight

        add(trend, 14); add(structure_state, 18); add(bos, 13); add(choch_state, 11)
        if "Sell Side Sweep" in sweep_state: bull += 13
        elif "Buy Side Sweep" in sweep_state: bear += 13
        add(order_block, 13); add(breaker_block, 8); add(mitigation_block, 8); add(fvg_state, 10)
        if "Discount" in premium_state["zone"]: bull += 8
        elif "Premium" in premium_state["zone"]: bear += 8
        if "Strong Bullish" in displacement_state: bull += 10
        elif "Strong Bearish" in displacement_state: bear += 10
        elif "Moderate Bullish" in displacement_state: bull += 5
        elif "Moderate Bearish" in displacement_state: bear += 5
        if macd_now > signal_now: bull += 5
        else: bear += 5
        if 45 <= rsi_value <= 68 and macd_now > signal_now: bull += 6
        elif 32 <= rsi_value <= 55 and macd_now < signal_now: bear += 6

        direction = "LONG" if bull >= bear else "SHORT"
        long = direction == "LONG"
        stop = atr_state["long_stop"] if long else atr_state["short_stop"]
        tp1, tp2, tp3 = atr_state["long_tp"] if long else atr_state["short_tp"]
        risk = abs(close - stop); rr = round(abs(tp3 - close) / risk, 2) if risk else 0.0

        aligned = {
            "Trend aligned": (long and "Bullish" in trend) or (not long and "Bearish" in trend),
            "Market structure aligned": (long and "Bullish" in structure_state) or (not long and "Bearish" in structure_state),
            "BOS confirmation": (long and "Bullish" in bos) or (not long and "Bearish" in bos),
            "CHOCH confirmation": (long and "Bullish" in choch_state) or (not long and "Bearish" in choch_state),
            "Liquidity sweep confirmation": (long and "Sell Side Sweep" in sweep_state) or (not long and "Buy Side Sweep" in sweep_state),
            "Order Block confirmation": (long and "Bullish" in order_block) or (not long and "Bearish" in order_block),
            "Breaker Block confirmation": (long and "Bullish" in breaker_block) or (not long and "Bearish" in breaker_block),
            "Mitigation Block confirmation": (long and "Bullish" in mitigation_block) or (not long and "Bearish" in mitigation_block),
            "Fair Value Gap confirmation": (long and "Bullish" in fvg_state) or (not long and "Bearish" in fvg_state),
            "Premium/Discount location aligned": (long and "Discount" in premium_state["zone"]) or (not long and "Premium" in premium_state["zone"]),
            "Displacement confirmation": (long and ("Strong Bullish" in displacement_state or "Moderate Bullish" in displacement_state)) or (not long and ("Strong Bearish" in displacement_state or "Moderate Bearish" in displacement_state)),
            "Momentum aligned": (long and macd_now > signal_now and rsi_value < 72) or (not long and macd_now < signal_now and rsi_value > 28),
        }
        reasons = [f"✅ {k}" for k, v in aligned.items() if v]
        confirmations = len(reasons)

        directional_strength = abs(bull - bear)
        total_evidence = max(bull + bear, 1)
        dominance = abs(bull - bear) / total_evidence * 100
        score = 35 + directional_strength * 0.55 + dominance * 0.25
        conflicts = []; blockers = 0

        if not aligned["Trend aligned"]:
            score -= 7; conflicts.append("⚠️ Counter-trend context")
        if not aligned["Market structure aligned"]:
            score -= 7; blockers += 1; conflicts.append("⚠️ Structure conflicts with direction")
        if "Weak" in displacement_state:
            score -= 3; conflicts.append("⚠️ Weak displacement")
        if volume_ratio < 0.65:
            score -= 5; blockers += 1; conflicts.append(f"⚠️ Low relative volume ({volume_ratio}x)")
        elif volume_ratio < 0.85:
            score -= 2; conflicts.append(f"⚠️ Below-average volume ({volume_ratio}x)")
        if long and rsi_value >= 72:
            score -= 6; blockers += 1; conflicts.append(f"⚠️ RSI overbought ({rsi_value:.1f})")
        elif not long and rsi_value <= 28:
            score -= 6; blockers += 1; conflicts.append(f"⚠️ RSI oversold ({rsi_value:.1f})")
        if long and "Premium" in premium_state["zone"]:
            score -= 7; blockers += 1; conflicts.append("⚠️ LONG entry is in Premium")
        elif not long and "Discount" in premium_state["zone"]:
            score -= 7; blockers += 1; conflicts.append("⚠️ SHORT entry is in Discount")
        if rr < self.MIN_RR:
            score -= 10; blockers += 1; conflicts.append(f"⛔ RR below 1:{self.MIN_RR}")

        score = round(max(0.0, min(100.0, score)), 1)
        recommendation, execution_status = self._recommendation(direction, score, rr, confirmations, blockers)

        trigger = []
        if execution_status != "🟢 READY":
            if not aligned["BOS confirmation"] and not aligned["CHOCH confirmation"]:
                trigger.append("Wait for BOS or CHOCH in the setup direction")
            if "Weak" in displacement_state:
                trigger.append("Require a moderate/strong displacement candle")
            if volume_ratio < 0.85:
                trigger.append("Prefer volume recovery above 0.85x")
            if long and "Premium" in premium_state["zone"]:
                trigger.append("Prefer a pullback toward equilibrium/discount")
            if not long and "Discount" in premium_state["zone"]:
                trigger.append("Prefer a retracement toward equilibrium/premium")
            if not trigger:
                trigger.append("Wait for one additional independent confirmation")

        rr_component = min(rr / 4.0, 1.0) * 100
        ranking_score = round(score * 0.55 + rr_component * 0.20 + min(confirmations / 8, 1) * 100 * 0.20 + (5 if execution_status == "🟢 READY" else 0), 2)

        return {
            "price": close, "trend": trend, "structure": structure_state, "bos": bos, "choch": choch_state,
            "liquidity": liquidity_state, "sweep": sweep_state, "order_block": order_block,
            "breaker": breaker_block, "mitigation": mitigation_block, "fvg": fvg_state,
            "premium": premium_state, "volume": volume_state, "volume_ratio": volume_ratio,
            "displacement": displacement_state, "atr": atr_state, "ema50": ema50, "ema200": ema200,
            "rsi": rsi_value, "macd": macd_state, "entry": close, "stop": stop,
            "tp1": tp1, "tp2": tp2, "tp3": tp3, "rr": rr, "direction": direction,
            "bull_score": round(bull, 2), "bear_score": round(bear, 2), "score": score,
            "probability": score, "confidence": score, "confirmations": confirmations,
            "ranking_score": ranking_score, "quality": self._quality(score), "market_bias": self._bias(direction, score),
            "recommendation": recommendation, "execution_status": execution_status,
            "reasons": reasons + conflicts, "triggers": trigger, "blockers": blockers,
        }
