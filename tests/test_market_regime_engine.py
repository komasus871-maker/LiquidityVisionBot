import numpy as np
import pandas as pd

from services.analyzer import Analyzer
from services.market_regime import MarketRegimeEngine


def _frame(prices, noise=0.002):
    close = np.asarray(prices, dtype=float)
    spread = np.maximum(abs(close) * noise, 1e-8)
    return pd.DataFrame({
        "open": close,
        "high": close + spread,
        "low": close - spread,
        "close": close,
        "volume": np.full(len(close), 1000.0),
    })


def test_trending_regime_detected():
    prices = np.linspace(100, 150, 260) + np.sin(np.arange(260) / 8) * 0.3
    result = MarketRegimeEngine().analyze(_frame(prices))
    assert result["code"] == "TRENDING"
    assert result["direction"] == "LONG"
    assert result["allows_trend_entry"] is True


def test_choppy_regime_blocks_trend_entry():
    prices = 100 + np.sin(np.arange(260) * 1.35) * 1.2
    result = MarketRegimeEngine().analyze(_frame(prices))
    assert result["code"] in {"RANGING", "TRANSITION", "COMPRESSION"}
    assert result["allows_trend_entry"] is False


def test_analyzer_exposes_market_regime():
    prices = np.linspace(100, 145, 300) + np.sin(np.arange(300) / 9) * 0.2
    data = Analyzer().analyze(_frame(prices))
    assert "market_regime" in data
    assert data["market_regime"]["code"]
    if data["execution_status"] == "🟢 READY":
        assert data["market_regime"]["code"] == "TRENDING"
        assert data["market_regime"]["direction"] == data["direction"]
