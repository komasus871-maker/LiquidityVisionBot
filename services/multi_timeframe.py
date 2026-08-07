from services.market import Market
from services.analyzer import Analyzer
from services.analysis_runtime import run_analysis
from services.market_intelligence import MarketIntelligenceEngine


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

    async def analyze_intelligence(self, symbol, side="NEUTRAL"):
        """Build the explicit 4H -> 1H -> 15M research hierarchy."""
        frames = {}
        for timeframe in ("4h", "1h", "15m"):
            frames[timeframe] = await self.market.get_klines(symbol=symbol, interval=timeframe)
        setup = await run_analysis(
            self.analyzer, frames["1h"], symbol=symbol, timeframe="1h",
            source="multi_timeframe_intelligence",
        )
        direction = side if side in {"LONG", "SHORT"} else setup.get("direction", "NEUTRAL")
        return MarketIntelligenceEngine().analyze_hierarchy(
            frames,
            side=direction,
            plan=setup,
        )
