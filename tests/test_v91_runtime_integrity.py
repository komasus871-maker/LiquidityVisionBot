from __future__ import annotations

import importlib

import pandas as pd

from services.brain import Brain
from services.release_integrity import validate_release
from utils.indicators import ema, macd, rsi
from version import APP_VERSION


def _frame(rows: int = 260) -> pd.DataFrame:
    close = [100 + i * 0.1 + ((i % 7) - 3) * 0.03 for i in range(rows)]
    return pd.DataFrame({"close": close})


def test_release_integrity_and_version():
    report = validate_release(required_modules=("services.brain", "services.unified_core"))
    assert APP_VERSION == "9.9.3"
    assert report.valid, report.as_dict()


def test_indicators_return_stable_series_without_optional_backend(monkeypatch):
    import utils.indicators as indicators
    monkeypatch.setattr(indicators, "EMAIndicator", None)
    monkeypatch.setattr(indicators, "RSIIndicator", None)
    monkeypatch.setattr(indicators, "MACD", None)
    frame = _frame()
    assert pd.notna(ema(frame, 50).iloc[-1])
    assert 0 <= rsi(frame).iloc[-1] <= 100
    line, signal = macd(frame)
    assert pd.notna(line.iloc[-1]) and pd.notna(signal.iloc[-1])


def test_legacy_brain_facade_produces_scanner_contract():
    result = Brain().build({
        "direction": "LONG",
        "direction_score": 72,
        "execution_readiness": 68,
        "entry_quality": 70,
        "unified_decision": {"action": "WAIT", "score": 71},
        "reasons": ["✅ Bullish structure"],
    })
    assert result["signal"] == "WAIT"
    assert result["score"] == 71
    assert result["reasons"]
