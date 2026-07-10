class BreakerBlock:

    def __init__(self, df):

        self.df = df

    def bullish(self):

        candles = self.df.tail(50).reset_index(drop=True)

        for i in range(5, len(candles) - 2):

            candle = candles.iloc[i]

            if candle["close"] >= candle["open"]:
                continue

            broken = False

            for j in range(i + 1, len(candles)):

                if candles.iloc[j]["close"] > candle["high"]:

                    broken = True
                    break

            if not broken:
                continue

            price = float(candles.iloc[-1]["close"])

            if candle["low"] <= price <= candle["high"]:

                return {

                    "type": "bullish",

                    "low": float(candle["low"]),

                    "high": float(candle["high"])

                }

        return None

    def bearish(self):

        candles = self.df.tail(50).reset_index(drop=True)

        for i in range(5, len(candles) - 2):

            candle = candles.iloc[i]

            if candle["close"] <= candle["open"]:
                continue

            broken = False

            for j in range(i + 1, len(candles)):

                if candles.iloc[j]["close"] < candle["low"]:

                    broken = True
                    break

            if not broken:
                continue

            price = float(candles.iloc[-1]["close"])

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