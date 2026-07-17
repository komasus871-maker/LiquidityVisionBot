"""Compatibility facade for handlers and background engines."""
from __future__ import annotations

from typing import Any

from .cache import analysis_cache


class UnifiedAnalysisFacade:
    @staticmethod
    def cache_stats() -> dict[str, Any]:
        return analysis_cache.stats()

    @staticmethod
    def invalidate() -> None:
        analysis_cache.clear()


unified_analysis = UnifiedAnalysisFacade()
