class FVG:

    def __init__(self, df):

        self.df = df

    def bullish(self):

        candles = self.df.reset_index(drop=True)

        gaps = []

        for i in range(2, len(candles)):

            c1 = candles.iloc[i - 2]
            c2 = candles.iloc[i - 1]
            c3 = candles.iloc[i]

            if c1["high"] < c3["low"]:

                gaps.append({

                    "type": "bullish",

                    "low": float(c1["high"]),

                    "high": float(c3["low"]),

                    "index": i

                })

        return gaps

    def bearish(self):

        candles = self.df.reset_index(drop=True)

        gaps = []

        for i in range(2, len(candles)):

            c1 = candles.iloc[i - 2]
            c2 = candles.iloc[i - 1]
            c3 = candles.iloc[i]

            if c1["low"] > c3["high"]:

                gaps.append({

                    "type": "bearish",

                    "low": float(c3["high"]),

                    "high": float(c1["low"]),

                    "index": i

                })

        return gaps

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