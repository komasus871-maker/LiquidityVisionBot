class TrendAlignment:

    @staticmethod
    def analyze(results):

        bullish = 0

        bearish = 0

        for text in results.values():

            if "Bullish" in text:

                bullish += 1

            else:

                bearish += 1

        if bullish == 5:

            return "🔥 Perfect Bullish Alignment"

        if bullish >= 4:

            return "🟢 Strong Bullish"

        if bearish == 5:

            return "🔥 Perfect Bearish Alignment"

        if bearish >= 4:

            return "🔴 Strong Bearish"

        return "🟡 Mixed Market"