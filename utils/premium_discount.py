import numpy as np


class PremiumDiscount:

    def __init__(self, df):

        self.df = df

    def swing_high(self):

        highs = self.df["high"].values

        for i in range(len(highs) - 3, 2, -1):

            if (
                highs[i] > highs[i - 1]
                and highs[i] > highs[i - 2]
                and highs[i] > highs[i + 1]
                and highs[i] > highs[i + 2]
            ):

                return float(highs[i])

        return float(max(highs[-30:]))

    def swing_low(self):

        lows = self.df["low"].values

        for i in range(len(lows) - 3, 2, -1):

            if (
                lows[i] < lows[i - 1]
                and lows[i] < lows[i - 2]
                and lows[i] < lows[i + 1]
                and lows[i] < lows[i + 2]
            ):

                return float(lows[i])

        return float(min(lows[-30:]))

    def equilibrium(self):

        high = self.swing_high()

        low = self.swing_low()

        return (high + low) / 2

    def zone(self):

        price = float(self.df["close"].iloc[-1])

        eq = self.equilibrium()

        if price > eq:

            return "🔴 Premium"

        elif price < eq:

            return "🟢 Discount"

        return "🟡 Equilibrium"

    def premium_percent(self):

        high = self.swing_high()

        low = self.swing_low()

        price = float(self.df["close"].iloc[-1])

        rng = high - low

        if rng == 0:

            return 50

        percent = ((price - low) / rng) * 100

        return round(percent, 2)

    def analyze(self):

        return {

            "zone": self.zone(),

            "equilibrium": round(
                self.equilibrium(),
                2
            ),

            "premium": self.premium_percent(),

            "high": round(
                self.swing_high(),
                2
            ),

            "low": round(
                self.swing_low(),
                2
            )

        }