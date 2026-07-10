class Displacement:

    def __init__(self, df):

        self.df = df

    def body(self, candle):

        return abs(

            candle["close"]

            -

            candle["open"]

        )

    def range(self, candle):

        return (

            candle["high"]

            -

            candle["low"]

        )

    def average_body(self, period=20):

        candles = self.df.tail(period)

        bodies = []

        for _, candle in candles.iterrows():

            bodies.append(

                self.body(candle)

            )

        return sum(bodies) / len(bodies)

    def bullish(self):

        candle = self.df.iloc[-1]

        avg = self.average_body()

        return (

            candle["close"] >

            candle["open"]

            and

            self.body(candle)

            >

            avg * 2

        )

    def bearish(self):

        candle = self.df.iloc[-1]

        avg = self.average_body()

        return (

            candle["close"] <

            candle["open"]

            and

            self.body(candle)

            >

            avg * 2

        )

    def strength(self):

        candle = self.df.iloc[-1]

        rng = self.range(candle)

        if rng == 0:

            return 0

        return round(

            self.body(candle)

            /

            rng

            *

            100,

            2

        )

    def analyze(self):

        if self.bullish():

            return (

                f"🟢 Bullish Displacement "

                f"({self.strength()}%)"

            )

        if self.bearish():

            return (

                f"🔴 Bearish Displacement "

                f"({self.strength()}%)"

            )

        return (

            f"⚪ Weak Displacement "

            f"({self.strength()}%)"

        )