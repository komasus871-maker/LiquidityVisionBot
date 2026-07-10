class MitigationBlock:

    def __init__(self, df):

        self.df = df

    def bullish(self):

        candles = self.df.tail(50).reset_index(drop=True)

        price = float(candles.iloc[-1]["close"])

        for i in range(len(candles) - 3):

            candle = candles.iloc[i]

            if candle["close"] >= candle["open"]:
                continue

            impulse = candles.iloc[i + 1]

            if impulse["close"] <= candle["high"]:
                continue

            if candle["low"] <= price <= candle["high"]:

                return {

                    "type": "bullish",

                    "low": float(candle["low"]),

                    "high": float(candle["high"])

                }

        return None

    def bearish(self):

        candles = self.df.tail(50).reset_index(drop=True)

        price = float(candles.iloc[-1]["close"])

        for i in range(len(candles) - 3):

            candle = candles.iloc[i]

            if candle["close"] <= candle["open"]:
                continue

            impulse = candles.iloc[i + 1]

            if impulse["close"] >= candle["low"]:
                continue

            if candle["low"] <= price <= candle["high"]:

                return {

                    "type": "bearish",

                    "low": float(candle["low"]),

                    "high": float(candle["high"])

                }

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