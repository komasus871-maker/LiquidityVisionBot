from __future__ import annotations

from datetime import datetime, timezone

import pytest


@pytest.fixture()
def v104_market_db(tmp_path, monkeypatch):
    monkeypatch.setattr("database.database.USE_POSTGRES", False)
    monkeypatch.setattr("database.database.DATABASE_NAME", tmp_path / "v104-market.db")
    from database.database import create_tables
    create_tables()
    return tmp_path / "v104-market.db"


def _levels(mid: float = 100.0, rows: int = 25):
    bids = [[mid - .01 - index * .01, 10 + index] for index in range(rows)]
    asks = [[mid + .01 + index * .01, 9 + index] for index in range(rows)]
    return bids, asks


@pytest.mark.asyncio
async def test_bingx_depth_accepts_known_nested_and_mapping_envelopes():
    from services.exchanges.bingx_swap import BingXSwapAdapter
    from services.exchanges.models import ExchangeCredentials

    adapter = BingXSwapAdapter(ExchangeCredentials("", ""))
    bids, asks = _levels(rows=8)
    payloads = [
        {"bids": bids, "asks": asks, "T": 10},
        {"data": {"depth": {"bids": bids, "asks": asks, "timestamp": 11}}},
        {"orderBook": {"bids": [{"p": p, "q": q} for p, q in bids],
                       "asks": [{"price": p, "size": q} for p, q in asks]}},
    ]
    for payload in payloads:
        async def request(*args, _payload=payload, **kwargs):
            return _payload
        adapter._request = request
        book = await adapter.market_depth("BTC-USDT", limit=8)
        assert len(book["bids"]) == 8 and len(book["asks"]) == 8
        assert float(book["bids"][0][0]) < float(book["asks"][0][0])
        diagnostic = adapter.public_diagnostics("DEPTH", "BTCUSDT")
        assert diagnostic["request_attempted"] and diagnostic["http_success"]
        assert diagnostic["payload_valid"] and diagnostic["rows_valid"] and diagnostic["normalized"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload,code",
    [({"data": {"unexpected": []}}, "DEPTH_PAYLOAD_SCHEMA_INVALID"),
     ({"bids": [], "asks": []}, "DEPTH_ROWS_EMPTY_OR_INVALID"),
     ({"bids": [[101, 1]], "asks": [[100, 1]]}, "DEPTH_BOOK_CROSSED")],
)
async def test_bingx_depth_malformed_empty_and_crossed_are_actionable(payload, code):
    from services.exchanges.bingx_swap import BingXSwapAdapter
    from services.exchanges.models import ExchangeCredentials

    adapter = BingXSwapAdapter(ExchangeCredentials("", ""))
    async def request(*args, **kwargs):
        return payload
    adapter._request = request
    with pytest.raises(Exception):
        await adapter.market_depth("BTCUSDT")
    diagnostic = adapter.public_diagnostics("DEPTH", "BTCUSDT")
    assert diagnostic["rejection_code"] == code
    assert diagnostic["request_attempted"] and diagnostic["http_success"]


def test_pipeline_diagnostics_persist_stage_and_failure_timestamps(v104_market_db):
    from services.market_intelligence_repository import MarketIntelligenceRepository

    repo = MarketIntelligenceRepository()
    for stage in ("request_attempted", "http_success", "payload_valid", "rows_valid",
                  "normalized", "aggregate_created", "persist_attempted", "persist_success"):
        repo.record_pipeline_stage(symbol="BTCUSDT", source_type="DEPTH", provider="TEST", stage=stage)
    report = repo.pipeline_diagnostics("BTCUSDT", "DEPTH", "TEST")
    assert report["depth_request_attempted"] == 1
    assert report["depth_persist_success"] == 1 and report["depth_last_success_at"]
    repo.record_pipeline_stage(symbol="BTCUSDT", source_type="DEPTH", provider="TEST",
                               stage="rows_valid", success=False,
                               rejection_code="DEPTH_ROWS_EMPTY_OR_INVALID")
    failed = repo.pipeline_diagnostics("BTCUSDT", "DEPTH", "TEST")
    assert failed["depth_rejection_code"] == "DEPTH_ROWS_EMPTY_OR_INVALID"
    assert failed["depth_last_failure_at"]


def test_microstructure_v3_is_bounded_fresh_and_quality_explicit():
    from services.market_intelligence import OrderBookMicrostructureEngine, contains_raw_order_book

    bids, asks = _levels(rows=25)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    snapshots = []
    for index in range(5):
        snapshots.append({"bids": [[p + index * .001, q] for p, q in bids],
                          "asks": [[p + index * .001, q] for p, q in asks],
                          "timestamp": now_ms + index})
    result = OrderBookMicrostructureEngine().analyze(snapshots, max_levels=50, max_snapshots=5)
    assert result["version"] == "microstructure-v3" and result["status"] == "AVAILABLE"
    assert set(result["depth_bands"]) == {"0.05", "0.1", "0.25", "0.5"}
    assert result["microstructure_quality_state"] in {"HIGH", "MODERATE", "LOW"}
    assert result["sampling_completeness"] == 100
    assert result["actor_identity_claimed"] is False and result["manipulation_claimed"] is False
    assert result["raw_book_persisted"] is False and not contains_raw_order_book(result)


def test_microstructure_v3_stale_and_missing_are_not_neutral(monkeypatch):
    from services.market_intelligence import OrderBookMicrostructureEngine, SignalQualityEngine

    bids, asks = _levels(rows=25)
    stale = OrderBookMicrostructureEngine().analyze(
        [{"bids": bids, "asks": asks, "timestamp": 1}] * 5)
    assert stale["microstructure_quality_state"] == "STALE"
    quality = SignalQualityEngine().analyze(
        side="LONG", plan={"entry": 100, "stop": 95, "rr": 2},
        data_quality={"status": "GOOD"},
        level_map={"current_price": 100, "atr": 2, "levels": [], "clusters": []},
        liquidity={"clusters": []},
        structure={"break": "CLOSE_CONFIRMED_BREAK", "quality": 80},
        zones={"fvg": [], "order_blocks": []},
        momentum={"score": 75, "state": "ACCELERATING"},
        trend={"direction": "BULLISH"}, story={"state": "TREND_CONTINUATION"},
        microstructure={"status": "UNAVAILABLE"},
        relative_strength={"status": "UNAVAILABLE"}, funding_oi={})
    assert quality["family_scores"]["MICROSTRUCTURE"] is None
    assert "MICROSTRUCTURE" in quality["unavailable_families"]


def test_readiness_v4_and_strategy_fusion_v3_remain_separate():
    from services.market_intelligence import MarketIntelligenceEngine

    quality = {"invalidation": {"valid_geometry": True}, "data_confidence": 85,
               "family_scores": {"LOCATION": 80, "MOMENTUM": 78, "MICROSTRUCTURE": 75,
                                 "STRUCTURE": 82, "INVALIDATION": 90,
                                 "TARGET_REALISM": 76, "EXECUTION_COST": 80},
               "contradicting_evidence": []}
    readiness = MarketIntelligenceEngine._entry_readiness(
        plan={"entry": 100, "stop": 95, "rr": 2}, quality=quality,
        microstructure={"status": "AVAILABLE", "microstructure_quality_state": "MODERATE"},
        momentum={"state": "ACCELERATING"}, structure={"break": "CLOSE_CONFIRMED_BREAK"},
        data_quality={"status": "GOOD"})
    assert readiness["version"] == "entry-readiness-v4"
    assert set(readiness["component_details"]) == set(readiness["components"])
    fusion = MarketIntelligenceEngine._strategy_fusion(
        {"BREAKOUT": 78, "LIQUIDITY_SMC": 73, "REVERSAL": 40})
    assert fusion["version"] == "strategy-fusion-v3"
    assert fusion["fusion_state"] in {"PRIMARY", "HYBRID"}
    assert fusion["primary"]["strategy"] == "BREAKOUT"
    assert fusion["secondary"]["strategy"] == "LIQUIDITY_SMC"
    assert fusion["score_is_probability"] is False


def test_calibration_and_strategy_diagnostics_are_honest_when_empty(v104_market_db):
    from services.market_intelligence_repository import MarketIntelligenceRepository

    repo = MarketIntelligenceRepository()
    quality = repo.quality_calibration_cohorts()
    readiness = repo.readiness_timing_cohorts()
    separation = repo.strategy_separation_diagnostics(7)
    assert [item["bucket"] for item in quality["cohorts"]] == [
        "0-20", "20-40", "40-60", "60-80", "80-100"]
    assert all(item["status"] == "INSUFFICIENT" for item in quality["cohorts"])
    assert {item["state"] for item in readiness["cohorts"]} == {
        "READY", "WAIT_STRUCTURE", "WAIT_CONFIRMATION", "WAIT_PULLBACK", "CHASING", "INVALID"}
    assert separation["snapshots"] == 0 and separation["forced_diversity"] is False
    assert separation["execution_authority"] is False


def test_copy_rejection_v3_keeps_production_thresholds_research_only(v104_market_db):
    from services.paper_copy_analytics import PaperCopyAnalyticsService

    report = PaperCopyAnalyticsService().report(999)
    assert report["version"] == "paper-copy-analytics-v3"
    assert set(report["guardrail_counterfactuals"]) == {
        "MAX_SLIPPAGE", "MAX_HEAT", "LOW_CONFIDENCE"}
    for cohort in report["guardrail_counterfactuals"].values():
        assert cohort["classifications"] == {
            "AVOIDED_LOSER": 0, "MISSED_WINNER": 0, "NEUTRAL_REJECTION": 0,
            "INSUFFICIENT_PATH_DATA": 0}
        assert cohort["policy_change_authority"] is False
        assert cohort["adaptive_slippage_research"]["production_threshold_changed"] is False
