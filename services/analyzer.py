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
    MIN_RR = 1.5
    STRONG_MIN_RR = 2.5

    @staticmethod
    def _volume_ratio(volume_state: str) -> float:
        match = re.search(r"\(([0-9.]+)x\)", volume_state)
        return float(match.group(1)) if match else 1.0

    @staticmethod
    def _quality(score: float) -> str:
        return (
            "⭐⭐⭐⭐⭐" if score >= 85 else
            "⭐⭐⭐⭐" if score >= 70 else
            "⭐⭐⭐" if score >= 58 else
            "⭐⭐" if score >= 45 else
            "⭐"
        )

    @staticmethod
    def _recommendation(direction: str, score: float, rr: float, confirmations: int) -> str:
        if rr < Analyzer.MIN_RR or score < 58 or confirmations < 4:
            return "🟡 WAIT"

        if direction == "LONG":
            if score >= 78 and rr >= Analyzer.STRONG_MIN_RR and confirmations >= 6:
                return "🔥 STRONG BUY"
            if score >= 65 and rr >= 2.0 and confirmations >= 5:
                return "🟢 BUY"
            return "🟢 WEAK BUY"

        if score >= 78 and rr >= Analyzer.STRONG_MIN_RR and confirmations >= 6:
            return "🔴 STRONG SELL"
        if score >= 65 and rr >= 2.0 and confirmations >= 5:
            return "🟠 SELL"
        return "🟠 WEAK SELL"

    def analyze(self, df):
        structure = Structure(df)
        choch = CHOCH(df)
        liquidity = Liquidity(df)
        sweep = Sweep(df)
        order_blocks = OrderBlocks(df)
        breaker = BreakerBlock(df)
        mitigation = MitigationBlock(df)
        fvg = FVG(df)
        premium = PremiumDiscount(df)
        volume = VolumeProfile(df)
        displacement = Displacement(df)
        atr = ATR(df)

        close = float(df["close"].iloc[-1])
        ema50 = float(ema(df, 50).iloc[-1])
        ema200 = float(ema(df, 200).iloc[-1])
        rsi_value = float(rsi(df).iloc[-1])
        macd_line, signal = macd(df)
        macd_now = float(macd_line.iloc[-1])
        signal_now = float(signal.iloc[-1])

        trend = "🟢 Bullish" if ema50 > ema200 else "🔴 Bearish"
        macd_state = "🟢 Bullish" if macd_now > signal_now else "🔴 Bearish"
        structure_state = structure.market_structure()
        bos = structure.bos()
        choch_state = choch.analyze()
        liquidity_state = liquidity.analyze()
        sweep_state = sweep.analyze()
        order_block = order_blocks.analyze()
        breaker_block = breaker.analyze()
        mitigation_block = mitigation.analyze()
        fvg_state = fvg.analyze()
        premium_state = premium.analyze()
        volume_state = volume.analyze()
        displacement_state = displacement.analyze()
        atr_state = atr.analyze()

        bull = 0.0
        bear = 0.0

        if "Bullish" in trend:
            bull += 15
        else:
            bear += 15

        if "Bullish" in structure_state:
            bull += 20
        elif "Bearish" in structure_state:
            bear += 20

        if "Bullish" in bos:
            bull += 15
        elif "Bearish" in bos:
            bear += 15

        if "Bullish" in choch_state:
            bull += 12
        elif "Bearish" in choch_state:
            bear += 12

        if "Sell Side Sweep" in sweep_state:
            bull += 15
        elif "Buy Side Sweep" in sweep_state:
            bear += 15

        if "Bullish" in order_block:
            bull += 15
        elif "Bearish" in order_block:
            bear += 15

        if "Bullish" in breaker_block:
            bull += 10
        elif "Bearish" in breaker_block:
            bear += 10

        if "Bullish" in mitigation_block:
            bull += 10
        elif "Bearish" in mitigation_block:
            bear += 10

        if "Bullish" in fvg_state:
            bull += 12
        elif "Bearish" in fvg_state:
            bear += 12

        if "Discount" in premium_state["zone"]:
            bull += 10
        elif "Premium" in premium_state["zone"]:
            bear += 10

        if "Bullish" in displacement_state:
            bull += 8
        elif "Bearish" in displacement_state:
            bear += 8

        if "Spike" in volume_state:
            if macd_now > signal_now:
                bull += 5
            else:
                bear += 5

        if macd_now > signal_now:
            bull += 5
        else:
            bear += 5

        if macd_now > signal_now and 45 <= rsi_value <= 70:
            bull += 8
        elif macd_now < signal_now and 30 <= rsi_value <= 55:
            bear += 8

        difference = bull - bear
        direction = "LONG" if difference > 0 else "SHORT"

        if direction == "LONG":
            stop = atr_state["long_stop"]
            tp1, tp2, tp3 = atr_state["long_tp"]
        else:
            stop = atr_state["short_stop"]
            tp1, tp2, tp3 = atr_state["short_tp"]

        risk = abs(close - stop)
        reward = abs(tp3 - close)
        rr = round(reward / risk, 2) if risk else 0.0

        raw_score = min(100.0, abs(difference))
        score = raw_score
        reasons = []
        conflicts = []

        long = direction == "LONG"
        trend_aligned = (long and "Bullish" in trend) or (not long and "Bearish" in trend)
        structure_aligned = (long and "Bullish" in structure_state) or (not long and "Bearish" in structure_state)
        bos_aligned = (long and "Bullish" in bos) or (not long and "Bearish" in bos)
        choch_aligned = (long and "Bullish" in choch_state) or (not long and "Bearish" in choch_state)
        sweep_aligned = (long and "Sell Side Sweep" in sweep_state) or (not long and "Buy Side Sweep" in sweep_state)
        ob_aligned = (long and "Bullish" in order_block) or (not long and "Bearish" in order_block)
        breaker_aligned = (long and "Bullish" in breaker_block) or (not long and "Bearish" in breaker_block)
        mitigation_aligned = (long and "Bullish" in mitigation_block) or (not long and "Bearish" in mitigation_block)
        fvg_aligned = (long and "Bullish" in fvg_state) or (not long and "Bearish" in fvg_state)
        zone_aligned = (long and "Discount" in premium_state["zone"]) or (not long and "Premium" in premium_state["zone"])
        displacement_aligned = (long and "Bullish" in displacement_state) or (not long and "Bearish" in displacement_state)
        momentum_aligned = (
            (long and macd_now > signal_now and 45 <= rsi_value <= 70) or
            (not long and macd_now < signal_now and 30 <= rsi_value <= 55)
        )

        labels = [
            (trend_aligned, "Trend aligned"),
            (structure_aligned, "Market structure aligned"),
            (bos_aligned, "BOS confirmation"),
            (choch_aligned, "CHOCH confirmation"),
            (sweep_aligned, "Liquidity sweep confirmation"),
            (ob_aligned, "Order Block confirmation"),
            (breaker_aligned, "Breaker Block confirmation"),
            (mitigation_aligned, "Mitigation Block confirmation"),
            (fvg_aligned, "Fair Value Gap confirmation"),
            (zone_aligned, "Premium/Discount location aligned"),
            (displacement_aligned, "Displacement confirmation"),
            (momentum_aligned, "Momentum aligned"),
        ]

        for aligned, label in labels:
            if aligned:
                reasons.append(f"✅ {label}")

        if not trend_aligned:
            score -= 10
            conflicts.append("⚠️ Counter-trend setup")
        if not structure_aligned:
            score -= 8
            conflicts.append("⚠️ Structure conflicts with trade direction")
        if "Weak Displacement" in displacement_state:
            score -= 5
            conflicts.append("⚠️ Weak displacement")

        volume_ratio = self._volume_ratio(volume_state)
        if volume_ratio < 0.8:
            score -= 5
            conflicts.append(f"⚠️ Low relative volume ({volume_ratio}x)")

        score = round(max(0.0, min(100.0, score)), 1)
        confirmations = len(reasons)
        recommendation = self._recommendation(direction, score, rr, confirmations)

        if rr < self.MIN_RR:
            conflicts.append(f"⛔ RR below minimum 1:{self.MIN_RR}")
        if confirmations < 4:
            conflicts.append("⛔ Not enough independent confirmations")
        if score < 58:
            conflicts.append("⛔ Setup score below execution threshold")

        reasons.extend(conflicts)

        structure_score = 100 if structure_aligned else 0
        liquidity_score = 100 if sweep_aligned else 50 if liquidity_state else 0
        momentum_score = 100 if momentum_aligned else 0
        rr_component = min(rr / 4.0, 1.0) * 100
        ranking_score = round(
            score * 0.45
            + rr_component * 0.20
            + structure_score * 0.15
            + liquidity_score * 0.10
            + momentum_score * 0.10,
            2,
        )

        return {
            "price": close,
            "trend": trend,
            "structure": structure_state,
            "bos": bos,
            "choch": choch_state,
            "liquidity": liquidity_state,
            "sweep": sweep_state,
            "order_block": order_block,
            "breaker": breaker_block,
            "mitigation": mitigation_block,
            "fvg": fvg_state,
            "premium": premium_state,
            "volume": volume_state,
            "volume_ratio": volume_ratio,
            "displacement": displacement_state,
            "atr": atr_state,
            "ema50": ema50,
            "ema200": ema200,
            "rsi": rsi_value,
            "macd": macd_state,
            "entry": close,
            "stop": stop,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "rr": rr,
            "direction": direction,
            "bull_score": round(bull, 2),
            "bear_score": round(bear, 2),
            "raw_score": raw_score,
            "score": score,
            "probability": score,
            "confidence": score,
            "confirmations": confirmations,
            "ranking_score": ranking_score,
            "quality": self._quality(score),
            "recommendation": recommendation,
            "reasons": reasons,
        }
