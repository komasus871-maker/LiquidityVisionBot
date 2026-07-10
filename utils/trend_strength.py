class TrendStrength:

    def __init__(self, df):

        self.df = df

    def calculate(self):

        ema50 = self.df.close.tail(50).mean()

        ema200 = self.df.close.tail(200).mean()

        difference = abs(

            ema50 - ema200

        )

        percent = (

            difference / ema200

        ) * 100

        if percent > 8:

            return "🔥 Very Strong"

        if percent > 5:

            return "🟢 Strong"

        if percent > 2:

            return "🟡 Medium"

        return "🔴 Weak"