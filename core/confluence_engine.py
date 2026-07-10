class ConfluenceEngine:

    def build(

        self,

        signals

    ):

        bull = 0

        bear = 0

        names = []

        for signal in signals:

            if not signal.active:

                continue

            names.append(

                signal.name

            )

            if signal.direction == "bull":

                bull += 1

            else:

                bear += 1

        total = bull + bear

        if total == 0:

            score = 0

        else:

            score = (

                max(

                    bull,

                    bear

                )

                /

                total

            ) * 100

        return {

            "bull": bull,

            "bear": bear,

            "score": round(

                score,

                2

            ),

            "signals": names

        }