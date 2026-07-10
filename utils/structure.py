import numpy as np


class Structure:

    def __init__(self, df):

        self.df = df

    def swing_highs(self):

        highs = self.df["high"].values

        points = []

        for i in range(2, len(highs) - 2):

            if (
                highs[i] > highs[i - 1]
                and highs[i] > highs[i - 2]
                and highs[i] > highs[i + 1]
                and highs[i] > highs[i + 2]
            ):
                points.append((i, highs[i]))

        return points

    def swing_lows(self):

        lows = self.df["low"].values

        points = []

        for i in range(2, len(lows) - 2):

            if (
                lows[i] < lows[i - 1]
                and lows[i] < lows[i - 2]
                and lows[i] < lows[i + 1]
                and lows[i] < lows[i + 2]
            ):
                points.append((i, lows[i]))

        return points

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