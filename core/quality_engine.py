class QualityEngine:

    def calculate(

        self,

        confidence,

        confluence,

        pattern

    ):

        value = (

            confidence * 0.45 +

            confluence["score"] * 0.30 +

            pattern["winrate"] * 0.25

        )

        value = min(

            value,

            100

        )

        if value >= 90:

            stars = "⭐⭐⭐⭐⭐"

        elif value >= 75:

            stars = "⭐⭐⭐⭐"

        elif value >= 60:

            stars = "⭐⭐⭐"

        elif value >= 40:

            stars = "⭐⭐"

        else:

            stars = "⭐"

        return {

            "score": round(

                value,

                2

            ),

            "stars": stars

        }