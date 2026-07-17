"""Ordered Unified Analysis Pipeline for LiquidityVisionBot v7.6."""
from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any, Iterable

from .cache import AnalysisCache, analysis_cache
from .context import AnalysisContext, AnalysisIdentity
from .services import (
    MarketService,
    StructureService,
    LiquidityService,
    VolumeService,
    MomentumService,
    RegimeService,
    TradeDNAFoundationService,
)


@dataclass(slots=True)
class PipelineResult:
    context: AnalysisContext
    cache_hit: bool = False

    @property
    def raw(self) -> dict[str, Any]:
        return self.context.raw


class UnifiedAnalysisPipeline:
    """Compute canonical market features once in dependency order."""

    def __init__(self, stages: Iterable[Any] | None = None, cache: AnalysisCache | None = None):
        self.stages = tuple(stages or (
            MarketService(),
            StructureService(),
            LiquidityService(),
            VolumeService(),
            MomentumService(),
            RegimeService(),
            TradeDNAFoundationService(),
        ))
        self.cache = cache or analysis_cache

    @staticmethod
    def _prepare_frame(dataframe: Any) -> Any:
        if "confirm" in dataframe.columns:
            confirmed = dataframe[dataframe["confirm"].astype(str) == "1"]
            if len(confirmed) >= 220:
                return confirmed.copy()
        return dataframe

    def execute(
        self,
        dataframe: Any,
        *,
        symbol: str | None = None,
        timeframe: str | None = None,
        source: str = "market",
        use_cache: bool = True,
    ) -> PipelineResult:
        frame = self._prepare_frame(dataframe)
        fingerprint = self.cache.fingerprint(frame, namespace="unified-raw-v7.6")
        key = f"raw:{symbol or ''}:{timeframe or ''}:{fingerprint}"
        if use_cache:
            cached = self.cache.get(key)
            if isinstance(cached, dict) and "raw" in cached:
                context = AnalysisContext(
                    dataframe=frame,
                    identity=AnalysisIdentity(symbol=symbol, timeframe=timeframe, source=source, fingerprint=fingerprint),
                )
                for section in ("raw", "market", "structure", "liquidity", "volume", "momentum", "regime", "trade_dna"):
                    getattr(context, section).update(cached.get(section) or {})
                context.diagnostics.update(cached.get("diagnostics") or {})
                context.diagnostics["cache_hit"] = True
                return PipelineResult(context, cache_hit=True)

        context = AnalysisContext(
            dataframe=frame,
            identity=AnalysisIdentity(symbol=symbol, timeframe=timeframe, source=source, fingerprint=fingerprint),
        )
        timings: dict[str, float] = {}
        completed: list[str] = []
        for stage in self.stages:
            started = perf_counter()
            stage.run(context)
            timings[stage.name] = round((perf_counter() - started) * 1000, 3)
            completed.append(stage.name)
        context.diagnostics.update({
            "completed_stages": completed,
            "stage_timings_ms": timings,
            "cache_hit": False,
        })
        if use_cache:
            self.cache.set(key, {
                "raw": context.raw,
                "market": context.market,
                "structure": context.structure,
                "liquidity": context.liquidity,
                "volume": context.volume,
                "momentum": context.momentum,
                "regime": context.regime,
                "trade_dna": context.trade_dna,
                "diagnostics": context.diagnostics,
            })
        return PipelineResult(context, cache_hit=False)


unified_pipeline = UnifiedAnalysisPipeline()
