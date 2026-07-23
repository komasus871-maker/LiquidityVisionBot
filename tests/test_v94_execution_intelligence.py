from __future__ import annotations

from services.copy_execution_intelligence import RejectionAnalytics


def test_rejection_analytics_builds_ranked_diagnostics():
    report = RejectionAnalytics.build([
        {"rejection_code": "LOW_CONFIDENCE", "symbol": "BTC", "timeframe": "1h"},
        {"rejection_code": "LOW_CONFIDENCE", "symbol": "SOL", "timeframe": "1h"},
        {"rejection_code": "MAX_POSITIONS", "symbol": "BTC", "timeframe": "15m"},
    ])
    assert report["total"] == 3
    assert report["top_code"] == "LOW_CONFIDENCE"
    assert report["top_code_count"] == 2
    assert report["by_code"][0].share_pct == 2 / 3 * 100
    assert report["by_symbol"][0].key == "BTC"
    assert report["by_timeframe"][0].key == "1h"


def test_empty_rejection_analytics_is_safe():
    report = RejectionAnalytics.build([])
    assert report["total"] == 0
    assert report["top_code"] is None
    assert report["by_code"] == []
