from __future__ import annotations

import json

from services.copy_similarity import CopySimilarityService
from version import APP_VERSION, RELEASE_NAME


def test_v98_release_metadata():
    assert APP_VERSION == "9.9.4"
    assert RELEASE_NAME == "Execution Journal & Idempotency Foundation"


def test_similarity_details_explain_groups_and_features():
    left = {
        "side": "LONG", "timeframe": "1h", "setup_key": "sweep_fvg",
        "market_regime": "trend", "bos": True, "choch": False,
        "sweep": True, "fvg": True, "session": "london", "rsi": 58.0,
        "confidence": 80.0, "rr": 3.0,
    }
    right = {
        **left,
        "session": "new_york", "rsi": 72.0, "rr": 1.2,
    }
    detail = CopySimilarityService.similarity_details(left, right)
    assert 0.0 < detail["overall"] < 1.0
    assert set(detail["group_scores"]) >= {"Structure", "Liquidity", "Market", "Indicators", "Execution"}
    assert "Direction" in detail["matched_features"]
    assert "Session" in detail["different_features"]
    assert "Rr" in detail["different_features"]


def test_report_uses_full_qualifying_sample_but_limits_replays(monkeypatch):
    service = CopySimilarityService()
    history = []
    for signal_id in range(2, 14):
        history.append({
            "signal_id": signal_id,
            "symbol": "ETHUSDT",
            "timeframe": "1h",
            "side": "LONG",
            "status": "CLOSED",
            "realized_r": 1.0 if signal_id % 2 == 0 else -1.0,
            "close_reason": "TP1" if signal_id % 2 == 0 else "STOP",
            "max_profit_pct": 2.0,
            "max_drawdown_pct": -0.8,
            "genome_json": json.dumps({
                "side": "LONG", "timeframe": "1h", "setup_key": "sweep",
                "bos": True, "sweep": True, "market_regime": "trend",
            }),
        })
    monkeypatch.setattr(service, "_history", lambda *_args, **_kwargs: history)
    report = service.report(
        7,
        {"id": 1, "symbol": "BTCUSDT", "side": "LONG", "timeframe": "1h", "setup_key": "sweep", "bos": True, "sweep": True, "market_regime": "trend"},
        limit=4,
    )
    assert report["found"] == 12
    assert report["shown"] == 4
    assert len(report["matches"]) == 4
    assert report["statistical_confidence"]["level"] == "MEDIUM"
    assert report["top_matching_features"]
    assert report["breakdown"]["Structure"] == 100.0


def test_grouped_genome_is_human_inspectable():
    grouped = CopySimilarityService.grouped_genome({
        "version": 1, "symbol": "BTCUSDT", "side": "LONG", "bos": True,
        "sweep": True, "rsi": 61.5, "confidence": 78.0,
    })
    assert grouped["Identity"]["symbol"] == "BTCUSDT"
    assert grouped["Structure"]["side"] == "LONG"
    assert grouped["Liquidity"]["sweep"] is True
    assert grouped["Indicators"]["rsi"] == 61.5
    assert grouped["Execution"]["confidence"] == 78.0
