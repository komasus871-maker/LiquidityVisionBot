from core.analysis import Analysis


class LiquidityEngine:

    def __init__(self):

        self.analysis = Analysis()

    def set_market(

        self,

        **kwargs

    ):

        self.analysis.market.update(

            kwargs

        )

    def set_smart_money(

        self,

        **kwargs

    ):

        self.analysis.smart_money.update(

            kwargs

        )

    def set_indicators(

        self,

        **kwargs

    ):

        self.analysis.indicators.update(

            kwargs

        )

    def set_context(

        self,

        **kwargs

    ):

        self.analysis.context.update(

            kwargs

        )

    def set_scores(

        self,

        **kwargs

    ):

        self.analysis.scores.update(

            kwargs

        )

    def set_trade(

        self,

        **kwargs

    ):

        self.analysis.trade.update(

            kwargs

        )

    def set_statistics(

        self,

        **kwargs

    ):

        self.analysis.statistics.update(

            kwargs

        )

    def build(self):

        return self.analysis