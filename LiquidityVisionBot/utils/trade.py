class Trade:

    @staticmethod
    def create(

        price,

        atr

    ):

        entry = price

        stop = price - atr * 2

        tp1 = price + atr * 2

        tp2 = price + atr * 4

        tp3 = price + atr * 6

        rr = (

            tp3 - entry

        ) / (

            entry - stop

        )

        return {

            "entry": round(entry,2),

            "stop": round(stop,2),

            "tp1": round(tp1,2),

            "tp2": round(tp2,2),

            "tp3": round(tp3,2),

            "rr": round(rr,2)

        }