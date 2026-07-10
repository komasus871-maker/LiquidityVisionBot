class Entry:

    @staticmethod
    def calculate(

        price,

        atr

    ):

        return {

            "entry": round(

                price,

                2

            ),

            "stop": round(

                price - atr * 2,

                2

            ),

            "tp1": round(

                price + atr * 2,

                2

            ),

            "tp2": round(

                price + atr * 4,

                2

            ),

            "tp3": round(

                price + atr * 6,

                2

            )

        }