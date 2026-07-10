from utils.indicators import (
    ema,
    rsi,
    macd,
)

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


class MarketEngine:

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

        trend = (
            "🟢 Bullish"
            if ema50 > ema200
            else "🔴 Bearish"
        )

        macd_state = (
            "🟢 Bullish"
            if macd_now > signal_now
            else "🔴 Bearish"
        )

        return {

            "price": close,

            "trend": trend,

            "structure": structure.market_structure(),

            "bos": structure.bos(),

            "choch": choch.analyze(),

            "liquidity": liquidity.analyze(),

            "sweep": sweep.analyze(),

            "order_block": order_blocks.analyze(),

            "breaker": breaker.analyze(),

            "mitigation": mitigation.analyze(),

            "fvg": fvg.analyze(),

            "premium": premium.analyze(),

            "volume": volume.analyze(),

            "displacement": displacement.analyze(),

            "atr": atr.analyze(),

            "ema50": ema50,

            "ema200": ema200,

            "rsi": rsi_value,

            "macd": macd_state,

            "macd_now": macd_now,

            "signal_now": signal_now

        }