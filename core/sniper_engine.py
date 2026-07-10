class SniperEngine:

    def validate(

        self,

        trend,

        fvg,

        breaker,

        orderblock,

        liquidity

    ):

        confirmations = 0

        if "Bullish" in trend:

            confirmations += 1

        if "Bullish" in fvg:

            confirmations += 1

        if "Bullish" in breaker:

            confirmations += 1

        if "Bullish" in orderblock:

            confirmations += 1

        if "Sell Side" in liquidity:

            confirmations += 1

        probability = confirmations * 20

        return {

            "confirmations": confirmations,

            "probability": probability

        }