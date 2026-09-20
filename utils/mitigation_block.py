import numpy as np


class MitigationBlock:

    def __init__(self, df):

        self.df = df

    def bullish(self):
        candles = self.df.tail(50)
        if len(candles) < 4:
            return None
        opens = candles["open"].to_numpy()
        highs = candles["high"].to_numpy()
        lows = candles["low"].to_numpy()
        closes = candles["close"].to_numpy()
        price = float(closes[-1])
        indices = np.arange(len(candles))
        next_closes = np.empty_like(closes)
        next_closes[:-1] = closes[1:]
        next_closes[-1] = np.nan
        matches = (
            (indices < len(candles) - 3)
            & (closes < opens)
            & (next_closes > highs)
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
        if len(candles) < 4:
            return None
        opens = candles["open"].to_numpy()
        highs = candles["high"].to_numpy()
        lows = candles["low"].to_numpy()
        closes = candles["close"].to_numpy()
        price = float(closes[-1])
        indices = np.arange(len(candles))
        next_closes = np.empty_like(closes)
        next_closes[:-1] = closes[1:]
        next_closes[-1] = np.nan
        matches = (
            (indices < len(candles) - 3)
            & (closes > opens)
            & (next_closes < lows)
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

                f"🟢 Bullish Mitigation "

                f"({bullish['low']:.2f}"

                f" - "

                f"{bullish['high']:.2f})"

            )

        bearish = self.bearish()

        if bearish:

            return (

                f"🔴 Bearish Mitigation "

                f"({bearish['low']:.2f}"

                f" - "

                f"{bearish['high']:.2f})"

            )

        return "⚪ No Mitigation Block"
