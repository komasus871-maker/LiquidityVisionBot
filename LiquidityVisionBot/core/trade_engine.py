from core.models import Decision
from core.models import MarketData
from core.models import Trade


class TradeEngine:

    def build(

        self,

        market: MarketData,

        decision: Decision

    ) -> Trade:

        atr = market.atr

        price = market.price

        if decision.bullish:

            stop = atr["long_stop"]

            tp1, tp2, tp3 = atr["long_tp"]

        else:

            stop = atr["short_stop"]

            tp1, tp2, tp3 = atr["short_tp"]

        risk = abs(

            price - stop

        )

        reward = abs(

            tp3 - price

        )

        rr = round(

            reward / risk,

            2

        ) if risk else 0

        return Trade(

            entry=price,

            stop=stop,

            tp1=tp1,

            tp2=tp2,

            tp3=tp3,

            rr=rr

        )