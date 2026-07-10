from core.models import (
    MarketData,
    Score,
    Decision,
    Trade
)


class ReportEngine:

    def build(

        self,

        market: MarketData,

        score: Score,

        decision: Decision,

        trade: Trade

    ):

        return {

            "price": market.price,

            "trend": market.trend,

            "structure": market.structure,

            "bos": market.bos,

            "choch": market.choch,

            "liquidity": market.liquidity,

            "sweep": market.sweep,

            "order_block": market.order_block,

            "breaker": market.breaker,

            "mitigation": market.mitigation,

            "fvg": market.fvg,

            "premium": market.premium,

            "volume": market.volume,

            "displacement": market.displacement,

            "atr": market.atr,

            "ema50": market.ema50,

            "ema200": market.ema200,

            "rsi": market.rsi,

            "macd": market.macd,

            "entry": trade.entry,

            "stop": trade.stop,

            "tp1": trade.tp1,

            "tp2": trade.tp2,

            "tp3": trade.tp3,

            "rr": trade.rr,

            "bull_score": score.bull,

            "bear_score": score.bear,

            "score": decision.confidence,

            "probability": decision.confidence,

            "confidence": decision.confidence,

            "quality": decision.quality,

            "recommendation": decision.recommendation,

            "reasons": score.reasons,

            "details": score.details

        }