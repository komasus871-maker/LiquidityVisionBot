import pandas as pd


class ATR:

    def __init__(self, df, period=14):

        self.df = df
        self.period = period

    def calculate(self):

        high = self.df["high"]
        low = self.df["low"]
        close = self.df["close"]

        tr = pd.concat(

            [

                high - low,

                (high - close.shift()).abs(),

                (low - close.shift()).abs()

            ],

            axis=1

        ).max(axis=1)

        atr = tr.rolling(

            self.period

        ).mean()

        return float(

            atr.iloc[-1]

        )

    def stop_loss(

        self,

        direction="long",

        multiplier=1.5

    ):

        price = float(

            self.df["close"].iloc[-1]

        )

        atr = self.calculate()

        if direction == "long":

            return round(

                price - atr * multiplier,

                2

            )

        return round(

            price + atr * multiplier,

            2

        )

    def take_profits(

        self,

        direction="long"

    ):

        price = float(

            self.df["close"].iloc[-1]

        )

        atr = self.calculate()

        if direction == "long":

            return (

                round(price + atr * 2, 2),

                round(price + atr * 4, 2),

                round(price + atr * 6, 2)

            )

        return (

            round(price - atr * 2, 2),

            round(price - atr * 4, 2),

            round(price - atr * 6, 2)

        )

    def analyze(self):

        return {

            "atr": round(

                self.calculate(),

                2

            ),

            "long_stop": self.stop_loss("long"),

            "short_stop": self.stop_loss("short"),

            "long_tp": self.take_profits("long"),

            "short_tp": self.take_profits("short")

        }