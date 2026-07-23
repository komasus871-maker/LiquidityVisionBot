"""Technical indicators with a dependency-free pandas fallback.

The production image may use :mod:`ta`, but the analysis core must remain
importable and testable when an optional indicator package is unavailable.
Both implementations expose identical pandas Series outputs.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

try:  # Optional acceleration/reference implementation.
    from ta.momentum import RSIIndicator
    from ta.trend import EMAIndicator, MACD
except ImportError:  # pragma: no cover - exercised in minimal runtime images.
    EMAIndicator = MACD = RSIIndicator = None


def _close(df: Any) -> pd.Series:
    if "close" not in df.columns:
        raise ValueError("indicator input must contain a 'close' column")
    series = pd.to_numeric(df["close"], errors="coerce").astype(float)
    if series.empty:
        raise ValueError("indicator input cannot be empty")
    return series


def ema(df: Any, period: int) -> pd.Series:
    if period <= 0:
        raise ValueError("EMA period must be positive")
    close = _close(df)
    if EMAIndicator is not None:
        return EMAIndicator(close=close, window=period).ema_indicator()
    return close.ewm(span=period, adjust=False, min_periods=period).mean()


def rsi(df: Any, period: int = 14) -> pd.Series:
    if period <= 0:
        raise ValueError("RSI period must be positive")
    close = _close(df)
    if RSIIndicator is not None:
        return RSIIndicator(close=close, window=period).rsi()

    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    alpha = 1.0 / period
    avg_gain = gain.ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    relative_strength = avg_gain / avg_loss.replace(0.0, float("nan"))
    result = 100.0 - (100.0 / (1.0 + relative_strength))
    result = result.mask((avg_loss == 0) & (avg_gain > 0), 100.0)
    return result.mask((avg_loss == 0) & (avg_gain == 0), 50.0)


def macd(df: Any) -> tuple[pd.Series, pd.Series]:
    close = _close(df)
    if MACD is not None:
        indicator = MACD(close)
        return indicator.macd(), indicator.macd_signal()

    fast = close.ewm(span=12, adjust=False, min_periods=12).mean()
    slow = close.ewm(span=26, adjust=False, min_periods=26).mean()
    line = fast - slow
    signal = line.ewm(span=9, adjust=False, min_periods=9).mean()
    return line, signal
