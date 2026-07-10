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

        bull = 0
        bear = 0
        reasons = []

        if "Bullish" in trend:
            bull += 15
            reasons.append("✅ Bull Trend")
        elif "Bearish" in trend:
            bear += 15
            reasons.append("✅ Bear Trend")

        if "Bullish" in structure_state:
            bull += 20
            reasons.append("✅ Bull Structure")
        elif "Bearish" in structure_state:
            bear += 20
            reasons.append("✅ Bear Structure")

        if "Bullish" in bos:
            bull += 15
            reasons.append("✅ Bull BOS")
        elif "Bearish" in bos:
            bear += 15
            reasons.append("✅ Bear BOS")

        if "Bullish" in choch_state:
            bull += 12
            reasons.append("✅ Bull CHOCH")
        elif "Bearish" in choch_state:
            bear += 12
            reasons.append("✅ Bear CHOCH")

        if "Sell Side Sweep" in sweep_state:
            bull += 15
            reasons.append("✅ Sell Side Sweep")
        elif "Buy Side Sweep" in sweep_state:
            bear += 15
            reasons.append("✅ Buy Side Sweep")

        if "Bullish" in order_block:
            bull += 15
            reasons.append("✅ Bull Order Block")
        elif "Bearish" in order_block:
            bear += 15
            reasons.append("✅ Bear Order Block")

        if "Bullish" in breaker_block:
            bull += 10
            reasons.append("✅ Bull Breaker")
        elif "Bearish" in breaker_block:
            bear += 10
            reasons.append("✅ Bear Breaker")

        if "Bullish" in mitigation_block:
            bull += 10
            reasons.append("✅ Bull Mitigation")
        elif "Bearish" in mitigation_block:
            bear += 10
            reasons.append("✅ Bear Mitigation")

        if "Bullish" in fvg_state:
            bull += 12
            reasons.append("✅ Bull FVG")
        elif "Bearish" in fvg_state:
            bear += 12
            reasons.append("✅ Bear FVG")

        if "Discount" in premium_state["zone"]:
            bull += 10
            reasons.append("✅ Discount")
        elif "Premium" in premium_state["zone"]:
            bear += 10
            reasons.append("✅ Premium")

        if "Bullish" in displacement_state:
            bull += 8
            reasons.append("✅ Bull Displacement")
        elif "Bearish" in displacement_state:
            bear += 8
            reasons.append("✅ Bear Displacement")

        if "Spike" in volume_state:
            if macd_now > signal_now:
                bull += 5
                reasons.append("✅ Bull Volume")
            else:
                bear += 5
                reasons.append("✅ Bear Volume")

        if macd_now > signal_now:
            bull += 5
        else:
            bear += 5

        if macd_now > signal_now and 45 <= rsi_value <= 70:
            bull += 8
            reasons.append("✅ Bull Momentum")
        elif macd_now < signal_now and 30 <= rsi_value <= 55:
            bear += 8
            reasons.append("✅ Bear Momentum")

        difference = bull - bear
        confidence = min(100, abs(difference))

        if difference >= 35:
            recommendation = "🔥 STRONG BUY"
        elif difference >= 15:
            recommendation = "🟢 BUY"
        elif difference <= -35:
            recommendation = "🔴 STRONG SELL"
        elif difference <= -15:
            recommendation = "🟠 SELL"
        else:
            recommendation = "🟡 WAIT"

        bullish = difference > 0

        if bullish:
            stop = atr_state["long_stop"]
            tp1, tp2, tp3 = atr_state["long_tp"]
        else:
            stop = atr_state["short_stop"]
            tp1, tp2, tp3 = atr_state["short_tp"]

        risk = abs(close - stop)
        reward = abs(tp3 - close)

        rr = round(reward / risk, 2) if risk else 0

        quality = (
            "⭐⭐⭐⭐⭐" if confidence >= 90 else
            "⭐⭐⭐⭐" if confidence >= 75 else
            "⭐⭐⭐" if confidence >= 55 else
            "⭐⭐" if confidence >= 35 else
            "⭐"
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
            "bull_score": round(bull, 2),
            "bear_score": round(bear, 2),
            "score": confidence,
            "probability": confidence,
            "confidence": confidence,
            "quality": quality,
            "recommendation": recommendation,
            "reasons": reasons
        }