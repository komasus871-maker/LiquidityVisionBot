import numpy as np


class FVG:

    def __init__(self, df):

        self.df = df

    def bullish(self):
        # This is the exact three-candle predicate used by the original loop.
        # NumPy removes tens of thousands of per-row Pandas ``iloc`` calls from
        # expanding-prefix research replays without changing gap identity,
        # ordering, bounds, or the causal information available at a prefix.
        highs = self.df["high"].to_numpy()
        lows = self.df["low"].to_numpy()
        if len(highs) < 3:
            return []
        indices = np.flatnonzero(highs[:-2] < lows[2:]) + 2
        return [
            {
                "type": "bullish",
                "low": float(highs[index - 2]),
                "high": float(lows[index]),
                "index": int(index),
            }
            for index in indices
        ]

    def bearish(self):
        lows = self.df["low"].to_numpy()
        highs = self.df["high"].to_numpy()
        if len(lows) < 3:
            return []
        indices = np.flatnonzero(lows[:-2] > highs[2:]) + 2
        return [
            {
                "type": "bearish",
                "low": float(highs[index]),
                "high": float(lows[index - 2]),
                "index": int(index),
            }
            for index in indices
        ]

    def active(self):

        price = float(self.df["close"].iloc[-1])

        bullish = self.bullish()

        bearish = self.bearish()

        for gap in reversed(bullish):

            if gap["low"] <= price <= gap["high"]:

                return gap

        for gap in reversed(bearish):

            if gap["low"] <= price <= gap["high"]:

                return gap

        return None

    def nearest(self):

        price = float(self.df["close"].iloc[-1])

        gaps = self.bullish() + self.bearish()

        if not gaps:

            return None

        nearest_gap = min(

            gaps,

            key=lambda x: min(

                abs(price - x["low"]),

                abs(price - x["high"])

            )

        )

        return nearest_gap

    def mitigated(self):

        candles = self.df.reset_index(drop=True)

        price = float(candles.iloc[-1]["close"])

        for gap in reversed(self.bullish()):

            if price > gap["high"]:

                return gap

        for gap in reversed(self.bearish()):

            if price < gap["low"]:

                return gap

        return None

    def analyze(self):

        active = self.active()

        if active:

            if active["type"] == "bullish":

                return (

                    f"🟢 Bullish Active FVG "

                    f"({active['low']:.2f}"

                    f" - "

                    f"{active['high']:.2f})"

                )

            return (

                f"🔴 Bearish Active FVG "

                f"({active['low']:.2f}"

                f" - "

                f"{active['high']:.2f})"

            )

        nearest = self.nearest()

        if nearest:

            if nearest["type"] == "bullish":

                return (

                    f"🟢 Bullish FVG "

                    f"({nearest['low']:.2f}"

                    f" - "

                    f"{nearest['high']:.2f})"

                )

            return (

                f"🔴 Bearish FVG "

                f"({nearest['low']:.2f}"

                f" - "

                f"{nearest['high']:.2f})"

            )

        return "⚪ No Fair Value Gap"
