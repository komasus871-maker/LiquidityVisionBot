from services.market import Market
from services.analyzer import Analyzer
from services.brain import Brain
from services.watchlist import WATCHLIST


class ScannerEngine:

    def __init__(self):

        self.market = Market()

        self.analyzer = Analyzer()

        self.brain = Brain()

    async def analyze_symbol(

        self,

        symbol

    ):

        candles = await self.market.get_klines(

            symbol

        )

        analysis = self.analyzer.analyze(

            candles

        )

        analysis["symbol"] = symbol

        return self.brain.build(

            analysis

        )

    async def scan(self):

        coins = []

        for symbol in WATCHLIST:

            try:

                coin = await self.analyze_symbol(

                    symbol

                )

                coins.append(

                    coin

                )

            except Exception as e:

                print(e)

        coins.sort(

            key=lambda x: x["score"],

            reverse=True

        )

        return coins