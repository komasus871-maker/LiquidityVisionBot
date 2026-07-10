import asyncio

from services.symbols import Symbols
from services.market import Market
from services.analyzer import Analyzer


class ScannerV2:

    def __init__(self):

        self.symbols = Symbols()

        self.market = Market()

        self.analyzer = Analyzer()

    async def analyze_symbol(self, symbol):

        try:

            df = await self.market.get_klines(symbol)

            result = self.analyzer.analyze(df)

            return symbol, result

        except Exception:

            return None

    async def scan(self):

        coins = await self.symbols.futures()

        tasks = [

            self.analyze_symbol(

                symbol

            )

            for symbol in coins

        ]

        results = await asyncio.gather(*tasks)

        return [

            coin

            for coin in results

            if coin

        ]