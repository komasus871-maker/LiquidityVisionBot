from engine.pipeline import Pipeline

from engine.stages.market_stage import MarketStage
from engine.stages.signal_stage import SignalStage
from engine.stages.scoring_stage import ScoringStage
from engine.stages.decision_stage import DecisionStage
from engine.stages.trade_stage import TradeStage
from engine.stages.report_stage import ReportStage


class LiquidityVisionEngine:

    def __init__(self):

        self.pipeline = Pipeline([

            MarketStage(),

            SignalStage(),

            ScoringStage(),

            DecisionStage(),

            TradeStage(),

            ReportStage(),

        ])

    def analyze(

        self,

        df

    ):

        return self.pipeline.run({

            "df": df

        })