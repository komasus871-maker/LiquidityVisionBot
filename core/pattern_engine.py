from core.similarity_engine import SimilarityEngine


class PatternEngine:

    def analyze(

        self,

        current,

        history

    ):

        matches = SimilarityEngine().find(

            current,

            history

        )

        if not matches:

            return {

                "samples": 0,

                "winrate": 0,

                "average_rr": 0

            }

        wins = 0

        total = len(matches)

        rr = 0

        for match in matches:

            signal = match["signal"]

            if signal["result"] != "SL":

                wins += 1

            rr += signal["rr"]

        return {

            "samples": total,

            "winrate": round(

                wins /

                total *

                100,

                2

            ),

            "average_rr": round(

                rr /

                total,

                2

            )

        }