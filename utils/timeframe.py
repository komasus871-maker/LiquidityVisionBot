"""Canonical market-candle timeframe vocabulary.

Keep provider spellings at the provider edge.  Runtime consumers use these
durations for both freshness and continuity checks.
"""
from __future__ import annotations

from typing import Any


TIMEFRAME_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1_800,
    "1h": 3_600,
    "2h": 7_200,
    "4h": 14_400,
    "6h": 21_600,
    "12h": 43_200,
    "1d": 86_400,
    "1w": 604_800,
}

# Retained for UI callers that intentionally expose a smaller supported list.
TIMEFRAMES = ["5m", "15m", "30m", "1h", "4h", "1d"]

_ALIASES = {
    "1min": "1m", "3min": "3m", "5min": "5m", "15min": "15m", "30min": "30m",
    "60m": "1h", "60min": "1h", "1hr": "1h", "1hour": "1h",
    "120m": "2h", "2hr": "2h", "2hour": "2h",
    "240m": "4h", "4hr": "4h", "4hour": "4h",
    "360m": "6h", "6hr": "6h", "6hour": "6h",
    "720m": "12h", "12hr": "12h", "12hour": "12h",
    "24h": "1d", "1day": "1d", "day": "1d", "7d": "1w", "1week": "1w",
}


def normalize_market_timeframe(value: Any) -> str | None:
    """Return a supported canonical candle interval, never a guessed fallback."""
    normalized = str(value or "").strip().lower().replace(" ", "")
    normalized = _ALIASES.get(normalized, normalized)
    return normalized if normalized in TIMEFRAME_SECONDS else None


def timeframe_seconds(value: Any) -> int | None:
    normalized = normalize_market_timeframe(value)
    return TIMEFRAME_SECONDS.get(normalized) if normalized else None
