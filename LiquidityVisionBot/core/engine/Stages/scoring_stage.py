from services.scoring_engine import ScoringEngine


class ScoringStage:

    def process(

        self,

        context

    ):

        engine = ScoringEngine()

        bull, bear, reasons = engine.calculate(

            context["signals"]

        )

        context["bull"] = bull

        context["bear"] = bear

        context["reasons"] = reasons

        return context