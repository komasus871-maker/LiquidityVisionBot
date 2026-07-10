import asyncio

from services.market import Market
from services.analyzer import Analyzer
from services.watchlist import WATCHLIST


class Scanner:

    def __init__(self):

        self.market = Market()

        self.analyzer = Analyzer()

    async def analyze_coin(self, symbol):

        try:

            df = await self.market.get_klines(symbol)

            result = self.analyzer.analyze(df)

            return {
                "symbol": symbol,
                "analysis": result,
                "confidence": result["confidence"],
                "recommendation": result["recommendation"],
                "rr": result["rr"],
            }

        except Exception:

            return None

    async def scan(self):

        tasks = [

            self.analyze_coin(

                coin

            )

            for coin in WATCHLIST

        ]

        results = await asyncio.gather(

            *tasks

        )

        valid = [r for r in results if r and r["recommendation"] != "🟡 WAIT"]
        return sorted(valid, key=lambda item: (item["confidence"], item["rr"]), reverse=True)