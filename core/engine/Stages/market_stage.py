from services.analyzer import Analyzer


class MarketStage:

    def process(

        self,

        context

    ):

        analyzer = Analyzer()

        analysis = analyzer.analyze(
            context["df"],
            symbol=context.get("symbol"),
            timeframe=context.get("timeframe"),
            source="legacy_engine",
        )

        context["analysis"] = analysis

        return context