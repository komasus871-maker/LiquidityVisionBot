class SignalStrength:

    def trend(

        self,

        ema50,

        ema200

    ):

        diff = abs(

            ema50 - ema200

        )

        avg = (

            ema50 + ema200

        ) / 2

        if avg == 0:

            return 0

        strength = (

            diff / avg

        ) * 10000

        return min(

            round(strength),

            100

        )

    def rsi(

        self,

        value

    ):

        if value <= 30:

            return 100

        if value >= 70:

            return 100

        return round(

            abs(

                value - 50

            ) * 4

        )

    def macd(

        self,

        macd,

        signal

    ):

        if signal == 0:

            return 0

        value = abs(

            macd - signal

        )

        strength = (

            value /

            abs(signal)

        ) * 100

        return min(

            round(strength),

            100

        )