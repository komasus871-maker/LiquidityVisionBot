class ExecutionEngine:

    def execute(

        self,

        decision,

        confidence,

        quality,

        session,

        news,

        pattern

    ):

        if not news["allow"]:

            return {

                "execute": False,

                "reason": "High Impact News"

            }

        if session["score"] < 40:

            return {

                "execute": False,

                "reason": "Poor Trading Session"

            }

        if confidence < 60:

            return {

                "execute": False,

                "reason": "Low Confidence"

            }

        if quality["score"] < 60:

            return {

                "execute": False,

                "reason": "Low Quality"

            }

        if pattern["samples"] >= 30:

            if pattern["winrate"] < 55:

                return {

                    "execute": False,

                    "reason": "Weak Historical Pattern"

                }

        return {

            "execute": True,

            "reason": decision["recommendation"]

        }