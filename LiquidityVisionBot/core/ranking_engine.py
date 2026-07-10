class RankingEngine:

    def build(

        self,

        decision,

        probability,

        quality,

        pattern

    ):

        rank = 0

        rank += probability

        rank += quality["score"]

        rank += pattern["winrate"]

        rank /= 3

        if rank >= 90:

            grade = "A+"

        elif rank >= 80:

            grade = "A"

        elif rank >= 70:

            grade = "B"

        elif rank >= 60:

            grade = "C"

        else:

            grade = "D"

        return {

            "rank": round(

                rank,

                2

            ),

            "grade": grade

        }