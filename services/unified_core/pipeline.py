"""Ordered Unified Analysis Pipeline for LiquidityVisionBot v7.6."""
from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any, Iterable

from services.data_integrity import DataIntegrityEngine

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
        self.integrity = DataIntegrityEngine()

    def execute(
        self,
        dataframe: Any,
        *,
        symbol: str | None = None,
        timeframe: str | None = None,
        source: str = "market",
        use_cache: bool = True,
    ) -> PipelineResult:
        attrs = getattr(dataframe, "attrs", {}) or {}
        # Market.get_klines marks every production provider frame. Direct
        # research/unit inputs can still exercise the analyzer without claiming
        # live freshness, but all production routes require timestamp truth.
        require_freshness = bool(attrs.get("market_data_provider"))
        effective_timeframe = timeframe or attrs.get("market_data_timeframe") or "1h"
        frame_result = self.integrity.prepare_market_frame(
            dataframe,
            timeframe=effective_timeframe,
            minimum_history=220,
            require_freshness=require_freshness,
        )
        frame = frame_result.frame
        fingerprint = self.cache.fingerprint(frame, namespace="unified-raw-v7.6")
        identity = AnalysisIdentity(symbol=symbol, timeframe=timeframe or effective_timeframe, source=source, fingerprint=fingerprint)
        if not frame_result.valid:
            context = AnalysisContext(dataframe=frame, identity=identity)
            context.diagnostics.update({
                "completed_stages": [],
                "stage_timings_ms": {},
                "cache_hit": False,
                "market_data_quality": frame_result.quality(),
            })
            return PipelineResult(context, cache_hit=False)
        key = f"raw:{symbol or ''}:{timeframe or ''}:{fingerprint}"
        if use_cache:
            cached = self.cache.get(key)
            if isinstance(cached, dict) and "raw" in cached:
                context = AnalysisContext(
                    dataframe=frame,
                    identity=identity,
                )
                for section in ("raw", "market", "structure", "liquidity", "volume", "momentum", "regime", "trade_dna"):
                    getattr(context, section).update(cached.get(section) or {})
                context.diagnostics.update(cached.get("diagnostics") or {})
                context.diagnostics.update({
                    "cache_hit": True,
                    "market_data_quality": frame_result.quality(),
                })
                return PipelineResult(context, cache_hit=True)

        context = AnalysisContext(
            dataframe=frame,
            identity=identity,
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
            "market_data_quality": frame_result.quality(),
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

    def execute_prevalidated_research(
        self,
        dataframe: Any,
        *,
        symbol: str | None = None,
        timeframe: str | None = None,
        source: str = "research",
    ) -> PipelineResult:
        """Run stages on a prefix of an already validated immutable dataset.

        This is deliberately separate from :meth:`execute`: production and
        arbitrary callers retain the full fail-closed market-data contract.
        Research runners may use this only after ``HistoricalReplayEngine``
        has validated and normalized the complete dataset; every chronological
        prefix of that frame therefore has the same valid candle contract.
        """
        frame = dataframe
        if not hasattr(frame, "columns") or len(frame) < 220:
            raise ValueError("prevalidated research prefix requires at least 220 candles")
        required = {"time", "open", "high", "low", "close", "volume"}
        if not required.issubset(set(frame.columns)):
            raise ValueError("prevalidated research prefix is missing canonical candle columns")
        effective_timeframe = timeframe or (getattr(frame, "attrs", {}) or {}).get("market_data_timeframe") or "1h"
        fingerprint = self.cache.fingerprint(frame, namespace="unified-raw-v7.6")
        context = AnalysisContext(
            dataframe=frame,
            identity=AnalysisIdentity(
                symbol=symbol,
                timeframe=timeframe or effective_timeframe,
                source=source,
                fingerprint=fingerprint,
            ),
        )
        timings: dict[str, float] = {}
        completed: list[str] = []
        for stage in self.stages:
            started = perf_counter()
            stage.run(context)
            timings[stage.name] = round((perf_counter() - started) * 1000, 3)
            completed.append(stage.name)
        attrs = getattr(frame, "attrs", {}) or {}
        quality = {
            "status": "VALID",
            "code": "OK",
            "reason": "Immutable replay dataset validated before prefix analysis",
            "valid": True,
            "contract_version": "market-data-v1",
            "provider": attrs.get("market_data_provider") or attrs.get("exchange") or "UNKNOWN",
            "symbol": attrs.get("market_data_symbol") or attrs.get("symbol") or symbol,
            "timeframe": effective_timeframe,
            "closed_candle_semantics": attrs.get("market_data_closed_semantics") or "CLOSED_ONLY_ASSUMED",
            "source_candles": len(frame),
            "usable_candles": len(frame),
        }
        context.diagnostics.update({
            "completed_stages": completed,
            "stage_timings_ms": timings,
            "cache_hit": False,
            "market_data_quality": quality,
            "research_prevalidated": True,
        })
        return PipelineResult(context, cache_hit=False)


unified_pipeline = UnifiedAnalysisPipeline()
