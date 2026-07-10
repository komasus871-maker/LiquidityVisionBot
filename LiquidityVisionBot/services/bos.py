class BOS:

    @staticmethod
    def detect(df):

        last = df.tail(15)

        high = last["high"].max()

        low = last["low"].min()

        close = last.iloc[-1]["close"]

        if close > high:

            return "Bullish BOS"

        if close < low:

            return "Bearish BOS"

        return "No BOS"