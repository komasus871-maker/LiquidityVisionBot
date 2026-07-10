from core.context_engine import ContextEngine
from core.scoring import ScoringEngine
from core.decision import DecisionEngine
from core.planner import TradePlanner


class CoreEngine:

    def __init__(self):

        self.context = ContextEngine()

        self.scoring = ScoringEngine()

        self.decision = DecisionEngine()

        self.planner = TradePlanner()

    def run(

        self,

        analysis,

        signals

    ):

        context = self.context.analyze(

            analysis

        )

        scores = self.scoring.calculate(

            signals

        )

        decision = self.decision.decide(

            scores["bull"],

            scores["bear"],

            context

        )

        trade = self.planner.build(

            decision,

            analysis["atr"],

            analysis["price"]

        )

        return {

            "context": context,

            "scores": scores,

            "decision": decision,

            "trade": trade

        }