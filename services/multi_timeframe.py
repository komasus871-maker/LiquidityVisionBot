from services.market import Market
from services.analyzer import Analyzer
from services.analysis_runtime import run_analysis


class MultiTimeframe:

    def __init__(self):

        self.market = Market()

        self.analyzer = Analyzer()

    async def analyze(self, symbol):

        timeframes = [

            "15m",

            "1h",

            "4h",

            "1d"

        ]

        result = {}

        for tf in timeframes:

            candles = await self.market.get_klines(

                symbol=symbol,

                interval=tf

            )

            result[tf] = await run_analysis(
                self.analyzer,
                candles,
                symbol=symbol,
                timeframe=tf,
                source="multi_timeframe",
            )

        return result