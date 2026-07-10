class TradePlanner:

    def build(self, analysis):

        price = analysis["price"]

        atr = analysis["atr"]

        bullish = analysis["probability"] >= 55

        if bullish:

            stop = atr["long_stop"]

            tp1, tp2, tp3 = atr["long_tp"]

        else:

            stop = atr["short_stop"]

            tp1, tp2, tp3 = atr["short_tp"]

        risk = abs(price - stop)

        reward = abs(tp3 - price)

        rr = 0

        if risk:

            rr = round(reward / risk, 2)

        return {

            "entry": price,

            "stop": stop,

            "tp1": tp1,

            "tp2": tp2,

            "tp3": tp3,

            "rr": rr

        }