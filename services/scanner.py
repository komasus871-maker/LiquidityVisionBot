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
            risks = [x.replace("⚠️ ", "").replace("⛔ ", "") for x in result["reasons"] if x.startswith(("⚠️", "⛔"))]
            return {
                "symbol": symbol,
                "analysis": result,
                "confidence": result["confidence"],
                "recommendation": result["recommendation"],
                "execution_status": result["execution_status"],
                "market_bias": result["market_bias"],
                "confirmations": result["confirmations"],
                "risk": risks[0] if risks else "No major blocker",
                "rr": result["rr"],
                "ranking_score": result["ranking_score"],
            }
        except Exception:
            return None

    async def scan(self):
        results = await asyncio.gather(*(self.analyze_coin(coin) for coin in WATCHLIST))
        valid = [r for r in results if r and r["recommendation"] != "⚪ NEUTRAL / NO EDGE"]
        return sorted(valid, key=lambda item: item["ranking_score"], reverse=True)
