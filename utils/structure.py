import numpy as np


class Structure:

    def __init__(self, df):

        self.df = df

    def swing_highs(self):
        highs = self.df["high"].values
        if len(highs) < 5:
            return []
        mask = (
            (highs[2:-2] > highs[1:-3])
            & (highs[2:-2] > highs[:-4])
            & (highs[2:-2] > highs[3:-1])
            & (highs[2:-2] > highs[4:])
        )
        indices = np.flatnonzero(mask) + 2
        return [(int(index), highs[index]) for index in indices]

    def swing_lows(self):
        lows = self.df["low"].values
        if len(lows) < 5:
            return []
        mask = (
            (lows[2:-2] < lows[1:-3])
            & (lows[2:-2] < lows[:-4])
            & (lows[2:-2] < lows[3:-1])
            & (lows[2:-2] < lows[4:])
        )
        indices = np.flatnonzero(mask) + 2
        return [(int(index), lows[index]) for index in indices]

    def market_structure(self):

        highs = self.swing_highs()
        lows = self.swing_lows()

        if len(highs) < 2 or len(lows) < 2:
            return "⚪ Unknown"

        last_high = highs[-1][1]
        prev_high = highs[-2][1]

        last_low = lows[-1][1]
        prev_low = lows[-2][1]

        if last_high > prev_high and last_low > prev_low:
            return "🟢 Bullish"

        if last_high < prev_high and last_low < prev_low:
            return "🔴 Bearish"

        return "🟡 Range"

    def bos(self):

        highs = self.swing_highs()
        lows = self.swing_lows()

        close = self.df["close"].iloc[-1]

        if highs:

            if close > highs[-1][1]:
                return f"🟢 Bullish BOS ({highs[-1][1]:.2f})"

        if lows:

            if close < lows[-1][1]:
                return f"🔴 Bearish BOS ({lows[-1][1]:.2f})"

        return "⚪ No BOS"

    def trend(self):

        return self.market_structure()

    def last_swing_high(self):

        highs = self.swing_highs()

        if highs:
            return highs[-1][1]

        return None

    def last_swing_low(self):

        lows = self.swing_lows()

        if lows:
            return lows[-1][1]

        return None
