class Risk:

    @staticmethod
    def calculate(

        price,

        atr

    ):

        stop = price - atr * 2

        tp1 = price + atr * 2

        tp2 = price + atr * 4

        rr = (

            tp2 - price

        ) / (

            price - stop

        )

        return {

            "stop": stop,

            "tp1": tp1,

            "tp2": tp2,

            "rr": rr

        }