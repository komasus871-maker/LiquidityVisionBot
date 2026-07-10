from services.trade_planner import TradePlanner


class TradeStage:

    def process(

        self,

        context

    ):

        planner = TradePlanner()

        trade = planner.build(

            context["analysis"],

            context["decision"]

        )

        context["trade"] = trade

        return context