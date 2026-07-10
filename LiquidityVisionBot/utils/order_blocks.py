import pandas as pd


class OrderBlocks:

    def __init__(self, df):

        self.df = df

    def bullish(self):

        candles = self.df.tail(40).reset_index(drop=True)

        for i in range(len(candles) - 3, 2, -1):

            candle = candles.iloc[i]

            previous = candles.iloc[i - 1]

            next_candle = candles.iloc[i + 1]

            bearish = candle["close"] < candle["open"]

            impulse = (
                next_candle["close"] >
                candle["high"]
            )

            if bearish and impulse:

                return {

                    "type": "bullish",

                    "high": float(candle["high"]),

                    "low": float(candle["low"]),

                    "index": i

                }

        return None

    def bearish(self):

        candles = self.df.tail(40).reset_index(drop=True)

        for i in range(len(candles) - 3, 2, -1):

            candle = candles.iloc[i]

            next_candle = candles.iloc[i + 1]

            bullish = candle["close"] > candle["open"]

            impulse = (
                next_candle["close"] <
                candle["low"]
            )

            if bullish and impulse:

                return {

                    "type": "bearish",

                    "high": float(candle["high"]),

                    "low": float(candle["low"]),

                    "index": i

                }

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