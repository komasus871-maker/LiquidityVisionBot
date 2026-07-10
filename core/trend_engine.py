class TrendEngine:

    def score(

        self,

        hierarchy

    ):

        score = hierarchy["score"]

        if hierarchy["aligned"] == 5:

            state = "Perfect"

        elif hierarchy["aligned"] >= 4:

            state = "Strong"

        elif hierarchy["aligned"] >= 3:

            state = "Medium"

        else:

            state = "Weak"

        return {

            "score": score,

            "state": state

        }