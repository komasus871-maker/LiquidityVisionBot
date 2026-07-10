class CHOCH:

    def __init__(self, df):

        self.df = df

    def analyze(self):

        highs = self.df["high"].values

        lows = self.df["low"].values

        if len(highs) < 6:
            return "⚪ Unknown"

        h1 = highs[-6]
        h2 = highs[-5]
        h3 = highs[-4]
        h4 = highs[-3]
        h5 = highs[-2]
        h6 = highs[-1]

        l1 = lows[-6]
        l2 = lows[-5]
        l3 = lows[-4]
        l4 = lows[-3]
        l5 = lows[-2]
        l6 = lows[-1]

        bullish = (
            h6 > h5
            and
            l6 > l5
            and
            h5 > h4
            and
            l5 > l4
        )

        bearish = (
            h6 < h5
            and
            l6 < l5
            and
            h5 < h4
            and
            l5 < l4
        )

        if bullish:
            return "🟢 Bullish CHOCH"

        if bearish:
            return "🔴 Bearish CHOCH"

        return "🟡 No CHOCH"