class ReportStage:

    def process(

        self,

        context

    ):

        analysis = context["analysis"]

        decision = context["decision"]

        trade = context["trade"]

        analysis.update({

            "entry": trade["entry"],

            "stop": trade["stop"],

            "tp1": trade["tp1"],

            "tp2": trade["tp2"],

            "tp3": trade["tp3"],

            "rr": trade["rr"],

            "bull_score": decision["bull"],

            "bear_score": decision["bear"],

            "score": decision["confidence"],

            "probability": decision["confidence"],

            "confidence": decision["confidence"],

            "quality": decision["quality"],

            "recommendation": decision["recommendation"],

            "reasons": context["reasons"]

        })

        return analysis