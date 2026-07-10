class VolumeProfile:

    def __init__(self, df):

        self.df = df

    def average_volume(self, period=20):

        return float(

            self.df["volume"]

            .tail(period)

            .mean()

        )

    def current_volume(self):

        return float(

            self.df["volume"].iloc[-1]

        )

    def relative_volume(self):

        avg = self.average_volume()

        current = self.current_volume()

        if avg == 0:

            return 1.0

        return round(

            current / avg,

            2

        )

    def volume_spike(self):

        return self.relative_volume() >= 2

    def climax(self):

        candle = self.df.iloc[-1]

        volume = candle["volume"]

        body = abs(

            candle["close"] -

            candle["open"]

        )

        rng = candle["high"] - candle["low"]

        if rng == 0:

            return False

        if (

            volume >=

            self.average_volume() * 2

            and

            body / rng < 0.35

        ):

            return True

        return False

    def absorption(self):

        candle = self.df.iloc[-1]

        previous = self.df.iloc[-2]

        if (

            candle["volume"] >

            previous["volume"]

            and

            abs(

                candle["close"]

                -

                candle["open"]

            )

            <

            abs(

                previous["close"]

                -

                previous["open"]

            )

        ):

            return True

        return False

    def analyze(self):

        rv = self.relative_volume()

        if self.climax():

            return (

                f"🔴 Volume Climax "

                f"({rv}x)"

            )

        if self.absorption():

            return (

                f"🟡 Absorption "

                f"({rv}x)"

            )

        if self.volume_spike():

            return (

                f"🟢 Volume Spike "

                f"({rv}x)"

            )

        return (

            f"⚪ Normal Volume "

            f"({rv}x)"

        )