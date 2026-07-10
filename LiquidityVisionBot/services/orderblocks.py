class OrderBlocks:

    @staticmethod
    def detect(df):

        result = []

        candles = df.tail(30)

        for i in range(1, len(candles)-1):

            prev = candles.iloc[i-1]

            cur = candles.iloc[i]

            nxt = candles.iloc[i+1]

            if (

                cur["close"] < cur["open"]

                and

                nxt["close"] > cur["high"]

            ):

                result.append({

                    "type": "Bullish",

                    "price": cur["high"]

                })

        return result