from __future__ import annotations

import asyncio
import logging
import os
import time

from services.analysis_runtime import run_analysis
from services.analyzer import Analyzer
from services.market import Market
from services.watchlist import WATCHLIST


class Scanner:
    """Bounded, cached market scanner safe for Render's free instance."""

    _scan_lock: asyncio.Lock | None = None
    _cached_results: list[dict] | None = None
    _cached_at: float = 0.0

    def __init__(self):
        self.market = Market()
        self.analyzer = Analyzer()
        self.concurrency = max(1, int(os.getenv("SCANNER_CONCURRENCY", "4")))
        self.cache_ttl = max(5, int(os.getenv("SCANNER_CACHE_TTL", "30")))

    @classmethod
    def _lock(cls) -> asyncio.Lock:
        if cls._scan_lock is None:
            cls._scan_lock = asyncio.Lock()
        return cls._scan_lock

    async def analyze_coin(self, symbol: str, semaphore: asyncio.Semaphore) -> dict | None:
        async with semaphore:
            try:
                df = await asyncio.wait_for(self.market.get_klines(symbol), timeout=30)
                result = await asyncio.wait_for(run_analysis(self.analyzer, df), timeout=30)
                risks = [
                    x.replace("⚠️ ", "").replace("⛔ ", "")
                    for x in result["reasons"]
                    if x.startswith(("⚠️", "⛔"))
                ]
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
                    "raw_ranking_score": result["ranking_score"],
                    "ranking_score": self._execution_score(result),
                    "edge": result["directional_edge"],
                    "category": result["opportunity_category"],
                    "entry_quality": result["entry_quality"],
                    "readiness": result["execution_readiness"],
                    "preferred_entry_low": result["preferred_entry_low"],
                    "preferred_entry_high": result["preferred_entry_high"],
                }
            except Exception as exc:
                logging.warning("Scanner failed for %s: %s", symbol, exc)
                return None

    @staticmethod
    def _execution_score(result: dict) -> float:
        direction = float(result.get("confidence") or 0)
        readiness = float(result.get("execution_readiness") or 0)
        entry = float(result.get("entry_quality") or 0)
        risk = float(result.get("risk_quality") or 50)
        category = str(result.get("opportunity_category") or "WATCHLIST")
        score = direction * 0.28 + readiness * 0.34 + entry * 0.28 + risk * 0.10
        penalties = {"REGIME_BLOCKED": 20, "REGIME_CONFIRMATION": 8, "WATCHLIST": 5}
        score -= penalties.get(category, 0)
        if entry < 25:
            score -= (25 - entry) * 0.7
        if readiness < 35:
            score -= (35 - readiness) * 0.5
        return round(max(0.0, min(100.0, score)), 1)

    async def scan(self, force: bool = False) -> list[dict]:
        now = time.monotonic()
        if (
            not force
            and self.__class__._cached_results is not None
            and now - self.__class__._cached_at < self.cache_ttl
        ):
            return list(self.__class__._cached_results)

        async with self._lock():
            now = time.monotonic()
            if (
                not force
                and self.__class__._cached_results is not None
                and now - self.__class__._cached_at < self.cache_ttl
            ):
                return list(self.__class__._cached_results)

            semaphore = asyncio.Semaphore(self.concurrency)
            results = await asyncio.gather(
                *(self.analyze_coin(coin, semaphore) for coin in WATCHLIST),
                return_exceptions=False,
            )
            valid = [result for result in results if result]
            valid.sort(key=lambda item: item["ranking_score"], reverse=True)
            self.__class__._cached_results = valid
            self.__class__._cached_at = time.monotonic()
            return list(valid)

    async def market_overview(self) -> dict:
        results = await self.scan()
        long_count = sum(r["direction"] == "LONG" for r in results)
        short_count = sum(r["direction"] == "SHORT" for r in results)
        ready_count = sum(r["category"] == "READY_NOW" for r in results)
        avg_score = round(sum(r["confidence"] for r in results) / len(results), 1) if results else 0
        avg_readiness = round(sum(r["readiness"] for r in results) / len(results), 1) if results else 0
        breadth = round(long_count / len(results) * 100, 1) if results else 50
        if breadth >= 65 and avg_readiness >= 55:
            regime = "🟢 Risk-On Expansion"
        elif breadth >= 65:
            regime = "🟡 Early Risk-On Recovery"
        elif breadth <= 35 and avg_readiness >= 55:
            regime = "🔴 Risk-Off Expansion"
        elif breadth <= 35:
            regime = "🟠 Defensive / Recovery Watch"
        else:
            regime = "🟡 Mixed / Rotation"
        return {
            "results": results,
            "long_count": long_count,
            "short_count": short_count,
            "ready_count": ready_count,
            "avg_score": avg_score,
            "avg_readiness": avg_readiness,
            "breadth": breadth,
            "regime": regime,
        }
