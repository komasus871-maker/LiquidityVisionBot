from core.models import Score


class ScoringEngine:

    def calculate(

        self,

        signals

    ):

        bull = 0.0

        bear = 0.0

        reasons = []

        details = []

        for signal in signals:

            if not signal.active:

                continue

            value = (

                signal.weight *

                signal.confidence *

                signal.strength

            ) / 10000

            details.append({

                "name": signal.name,

                "direction": signal.direction,

                "score": round(value, 2),

                "weight": signal.weight,

                "strength": signal.strength,

                "confidence": signal.confidence

            })

            if signal.direction == "bull":

                bull += value

            elif signal.direction == "bear":

                bear += value

            reasons.append(

                signal.reason

            )

        difference = bull - bear

        return Score(

            bull=round(

                bull,

                2

            ),

            bear=round(

                bear,

                2

            ),

            difference=round(

                difference,

                2

            ),

            reasons=reasons,

            details=details

        )