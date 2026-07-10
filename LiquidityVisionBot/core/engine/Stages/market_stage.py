from services.analyzer import Analyzer


class MarketStage:

    def process(

        self,

        context

    ):

        analyzer = Analyzer()

        analysis = analyzer.analyze(

            context["df"]

        )

        context["analysis"] = analysis

        return context