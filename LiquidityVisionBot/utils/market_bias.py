class MarketBias:

    @staticmethod
    def analyze(score):

        if score >= 90:

            return "🚀 Extremely Bullish"

        if score >= 75:

            return "🟢 Bullish"

        if score >= 60:

            return "🟡 Neutral"

        if score >= 40:

            return "🟠 Bearish"

        return "🔴 Extremely Bearish"