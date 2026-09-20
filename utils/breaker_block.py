import numpy as np


class BreakerBlock:

    def __init__(self, df):

        self.df = df

    def bullish(self):
        candles = self.df.tail(50)
        if len(candles) < 8:
            return None
        opens = candles["open"].to_numpy()
        highs = candles["high"].to_numpy()
        lows = candles["low"].to_numpy()
        closes = candles["close"].to_numpy()
        # Original semantics require any later close to break the candidate.
        later_max = np.maximum.accumulate(closes[::-1])[::-1]
        price = float(closes[-1])
        indices = np.arange(len(candles))
        matches = (
            (indices >= 5)
            & (indices < len(candles) - 2)
            & (closes < opens)
            & (later_max[np.minimum(indices + 1, len(candles) - 1)] > highs)
            & (lows <= price)
            & (price <= highs)
        )
        found = np.flatnonzero(matches)
        if found.size:
            index = int(found[0])
            return {"type": "bullish", "low": float(lows[index]), "high": float(highs[index])}

        return None

    def bearish(self):
        candles = self.df.tail(50)
        if len(candles) < 8:
            return None
        opens = candles["open"].to_numpy()
        highs = candles["high"].to_numpy()
        lows = candles["low"].to_numpy()
        closes = candles["close"].to_numpy()
        later_min = np.minimum.accumulate(closes[::-1])[::-1]
        price = float(closes[-1])
        indices = np.arange(len(candles))
        matches = (
            (indices >= 5)
            & (indices < len(candles) - 2)
            & (closes > opens)
            & (later_min[np.minimum(indices + 1, len(candles) - 1)] < lows)
            & (lows <= price)
            & (price <= highs)
        )
        found = np.flatnonzero(matches)
        if found.size:
            index = int(found[0])
            return {"type": "bearish", "low": float(lows[index]), "high": float(highs[index])}

        return None

    def analyze(self):

        bullish = self.bullish()

        if bullish:

            return (

                f"🟢 Bullish Breaker "

                f"({bullish['low']:.2f}"

                f" - "

                f"{bullish['high']:.2f})"

            )

        bearish = self.bearish()

        if bearish:

            return (

                f"🔴 Bearish Breaker "

                f"({bearish['low']:.2f}"

                f" - "

                f"{bearish['high']:.2f})"

            )

        return "⚪ No Breaker Block"
