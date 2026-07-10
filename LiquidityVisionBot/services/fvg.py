class FVG:

    @staticmethod
    def detect(df):

        gaps = []

        candles = df.reset_index(drop=True)

        for i in range(2, len(candles)):

            c1 = candles.iloc[i-2]

            c2 = candles.iloc[i-1]

            c3 = candles.iloc[i]

            if c3["low"] > c1["high"]:

                gaps.append({

                    "type": "Bullish",

                    "low": c1["high"],

                    "high": c3["low"]

                })

            elif c3["high"] < c1["low"]:

                gaps.append({

                    "type": "Bearish",

                    "high": c1["low"],

                    "low": c3["high"]

                })

        return gaps