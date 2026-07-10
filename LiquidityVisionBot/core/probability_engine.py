class ProbabilityEngine:

    def calculate(

        self,

        confidence,

        quality,

        pattern,

        confluence,

        session,

        execution

    ):

        score = 0

        score += confidence * 0.30

        score += quality["score"] * 0.25

        score += pattern["winrate"] * 0.20

        score += confluence["score"] * 0.15

        score += session["score"] * 0.10

        if not execution["execute"]:

            score *= 0.4

        return min(

            round(score,2),

            100

        )