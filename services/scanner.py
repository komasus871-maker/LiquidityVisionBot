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
                "direction": result["direction"],
                "confidence": result["confidence"],
                "recommendation": result["recommendation"],
                "execution_status": result["execution_status"],
                "market_bias": result["market_bias"],
                "confirmations": result["confirmations"],
                "risk": risks[0] if risks else "No major blocker",
                "rr": result["rr"],
                "ranking_score": result["ranking_score"],
                "edge": result["directional_edge"],
            }
        except Exception:
            return None

    async def scan(self):
        results = await asyncio.gather(*(self.analyze_coin(coin) for coin in WATCHLIST))
        valid = [r for r in results if r]
        valid.sort(key=lambda item: item["ranking_score"], reverse=True)
        return valid

    async def market_overview(self):
        results = await self.scan()
        long_count = sum(1 for r in results if r["direction"] == "LONG")
        short_count = sum(1 for r in results if r["direction"] == "SHORT")
        ready_count = sum(1 for r in results if r["execution_status"] == "🟢 READY")
        avg_score = round(sum(r["confidence"] for r in results) / len(results), 1) if results else 0
        breadth = round(long_count / len(results) * 100, 1) if results else 50
        regime = "🟢 Risk-On" if breadth >= 65 else "🔴 Risk-Off" if breadth <= 35 else "🟡 Mixed"
        return {
            "results": results,
            "long_count": long_count,
            "short_count": short_count,
            "ready_count": ready_count,
            "avg_score": avg_score,
            "breadth": breadth,
            "regime": regime,
        }
