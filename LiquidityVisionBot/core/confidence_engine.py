class ConfidenceEngine:

    def calculate(

        self,

        bull,

        bear,

        pattern,

        context,

        signals,

        mtf=None

    ):

        confidence = 0

        difference = abs(

            bull - bear

        )

        confidence += min(

            difference,

            35

        )

        confidence += min(

            len(signals) * 3,

            20

        )

        confidence += min(

            pattern["winrate"] / 5,

            20

        )

        confidence += min(

            context["confidence"],

            15

        )

        if mtf is not None:

            confidence += min(

                mtf["score"],

                10

            )

        confidence = min(

            confidence,

            100

        )

        return round(

            confidence,

            2

        )