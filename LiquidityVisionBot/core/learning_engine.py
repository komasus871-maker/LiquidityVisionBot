class LearningEngine:

    def update(

        self,

        weights,

        pattern

    ):

        if pattern["samples"] < 30:

            return weights

        updated = weights.copy()

        win = pattern["winrate"]

        factor = (

            win - 50

        ) / 100

        for key in updated:

            updated[key] = max(

                1,

                round(

                    updated[key] *

                    (

                        1 +

                        factor

                    )

                )

            )

        return updated