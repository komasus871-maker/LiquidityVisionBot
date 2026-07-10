class OptimizerEngine:

    def optimize(

        self,

        weights,

        statistics

    ):

        updated = weights.copy()

        for key in updated:

            if key not in statistics:

                continue

            stat = statistics[key]

            if stat["samples"] < 30:

                continue

            if stat["winrate"] > 65:

                updated[key] += 1

            elif stat["winrate"] < 45:

                updated[key] = max(

                    1,

                    updated[key] - 1

                )

        return updated