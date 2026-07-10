from services.decision_engine import DecisionEngine


class DecisionStage:

    def process(

        self,

        context

    ):

        engine = DecisionEngine()

        decision = engine.decide(

            context["bull"],

            context["bear"]

        )

        context["decision"] = decision

        return context