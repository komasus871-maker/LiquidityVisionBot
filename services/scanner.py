import asyncio

from services.market import Market
from services.analyzer import Analyzer
from services.watchlist import WATCHLIST


class Scanner:
    def __init__(self):
        self.market = Market(); self.analyzer = Analyzer()

    async def analyze_coin(self, symbol):
        try:
            df = await self.market.get_klines(symbol)
            result = self.analyzer.analyze(df)
            risks = [x.replace("⚠️ ", "").replace("⛔ ", "") for x in result["reasons"] if x.startswith(("⚠️", "⛔"))]
            return {
                "symbol": symbol, "analysis": result, "direction": result["direction"],
                "confidence": result["confidence"], "recommendation": result["recommendation"],
                "execution_status": result["execution_status"], "market_bias": result["market_bias"],
                "confirmations": result["confirmations"], "risk": risks[0] if risks else "No major blocker",
                "rr": result["rr"], "ranking_score": result["ranking_score"],
                "edge": result["directional_edge"], "category": result["opportunity_category"],
                "entry_quality": result["entry_quality"], "readiness": result["execution_readiness"],
                "preferred_entry_low": result["preferred_entry_low"], "preferred_entry_high": result["preferred_entry_high"],
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
        long_count = sum(r["direction"] == "LONG" for r in results)
        short_count = sum(r["direction"] == "SHORT" for r in results)
        ready_count = sum(r["category"] == "READY_NOW" for r in results)
        avg_score = round(sum(r["confidence"] for r in results) / len(results), 1) if results else 0
        avg_readiness = round(sum(r["readiness"] for r in results) / len(results), 1) if results else 0
        breadth = round(long_count / len(results) * 100, 1) if results else 50
        if breadth >= 65 and avg_readiness >= 55: regime = "🟢 Risk-On Expansion"
        elif breadth >= 65: regime = "🟡 Early Risk-On Recovery"
        elif breadth <= 35 and avg_readiness >= 55: regime = "🔴 Risk-Off Expansion"
        elif breadth <= 35: regime = "🟠 Defensive / Recovery Watch"
        else: regime = "🟡 Mixed / Rotation"
        return {"results": results, "long_count": long_count, "short_count": short_count,
                "ready_count": ready_count, "avg_score": avg_score, "avg_readiness": avg_readiness,
                "breadth": breadth, "regime": regime}
