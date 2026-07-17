"""Shared execution guard for CPU-heavy pandas analysis.

The analyzer is synchronous and can block aiogram's event loop for multiple
seconds.  All production call-sites should use :func:`run_analysis` so the
work runs in a thread and concurrency stays bounded on Render's small free
instance.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

_MAX_CONCURRENCY = max(1, int(os.getenv("ANALYSIS_CONCURRENCY", "2")))
_SEMAPHORE: asyncio.Semaphore | None = None


def _semaphore() -> asyncio.Semaphore:
    global _SEMAPHORE
    if _SEMAPHORE is None:
        _SEMAPHORE = asyncio.Semaphore(_MAX_CONCURRENCY)
    return _SEMAPHORE


async def run_analysis(analyzer: Any, dataframe: Any, **kwargs: Any) -> dict:
    """Run analysis off-loop and pass identity metadata into the unified core."""
    async with _semaphore():
        return await asyncio.to_thread(analyzer.analyze, dataframe, **kwargs)
