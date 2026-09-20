import pandas as pd
import numpy as np


class OrderBlocks:

    def __init__(self, df):

        self.df = df

    def bullish(self):
        candles = self.df.tail(40)
        if len(candles) < 6:
            return None
        opens = candles["open"].to_numpy()
        highs = candles["high"].to_numpy()
        lows = candles["low"].to_numpy()
        closes = candles["close"].to_numpy()
        indices = np.arange(len(candles))
        next_closes = np.empty_like(closes)
        next_closes[:-1] = closes[1:]
        next_closes[-1] = np.nan
        matches = (
            (indices >= 3)
            & (indices <= len(candles) - 3)
            & (closes < opens)
            & (next_closes > highs)
        )
        found = np.flatnonzero(matches)
        if found.size:
            index = int(found[-1])
            return {"type": "bullish", "high": float(highs[index]),
                    "low": float(lows[index]), "index": index}

        return None

    def bearish(self):
        candles = self.df.tail(40)
        if len(candles) < 6:
            return None
        opens = candles["open"].to_numpy()
        highs = candles["high"].to_numpy()
        lows = candles["low"].to_numpy()
        closes = candles["close"].to_numpy()
        indices = np.arange(len(candles))
        next_closes = np.empty_like(closes)
        next_closes[:-1] = closes[1:]
        next_closes[-1] = np.nan
        matches = (
            (indices >= 3)
            & (indices <= len(candles) - 3)
            & (closes > opens)
            & (next_closes < lows)
        )
        found = np.flatnonzero(matches)
        if found.size:
            index = int(found[-1])
            return {"type": "bearish", "high": float(highs[index]),
                    "low": float(lows[index]), "index": index}

        return None

    def active(self):

        price = float(self.df["close"].iloc[-1])

        bullish = self.bullish()

        bearish = self.bearish()

        if bullish:

            if bullish["low"] <= price <= bullish["high"]:

                return bullish

        if bearish:

            if bearish["low"] <= price <= bearish["high"]:

                return bearish

        return None

    def analyze(self):

        active = self.active()

        if active is None:

            return "⚪ No Active Order Block"

        if active["type"] == "bullish":

            return (
                f"🟢 Bullish OB "
                f"({active['low']:.2f}"
                f" - "
                f"{active['high']:.2f})"
            )

        return (
            f"🔴 Bearish OB "
            f"({active['low']:.2f}"
            f" - "
            f"{active['high']:.2f})"
        )
