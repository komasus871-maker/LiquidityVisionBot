class BacktestEngine:

    def run(

        self,

        candles,

        strategy

    ):

        results = []

        for i in range(

            250,

            len(candles)

        ):

            data = candles.iloc[:i]

            signal = strategy(

                data

            )

            results.append(

                signal

            )

        return results