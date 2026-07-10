class ReplayEngine:

    def replay(

        self,

        candles,

        callback

    ):

        history = []

        for candle in candles:

            history.append(

                candle

            )

            callback(

                history

            )