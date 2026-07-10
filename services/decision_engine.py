class DecisionEngine:

    BUY = "🟢 BUY"
    STRONG_BUY = "🔥 STRONG BUY"

    SELL = "🟠 SELL"
    STRONG_SELL = "🔴 STRONG SELL"

    WAIT = "🟡 WAIT"

    def decide(self, bull, bear):

        diff = bull - bear

        if diff >= 35:
            return self.STRONG_BUY

        if diff >= 15:
            return self.BUY

        if diff <= -35:
            return self.STRONG_SELL

        if diff <= -15:
            return self.SELL

        return self.WAIT