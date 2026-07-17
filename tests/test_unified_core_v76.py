from services.analyzer import Analyzer
from services.unified_core import analysis_cache, unified_pipeline
from tests.test_analyzer_regression import make_frame


def test_pipeline_exposes_ordered_context_sections():
    analysis_cache.clear()
    result = unified_pipeline.execute(make_frame(), symbol="BTC", timeframe="1h", use_cache=False)
    assert result.context.diagnostics["completed_stages"] == [
        "market", "structure", "liquidity", "volume", "momentum", "regime", "trade_dna"
    ]
    assert result.raw["price"]
    assert result.context.trade_dna["regime"]


def test_second_pipeline_call_uses_shared_cache():
    analysis_cache.clear()
    frame = make_frame()
    first = unified_pipeline.execute(frame, symbol="BTC", timeframe="1h")
    second = unified_pipeline.execute(frame, symbol="BTC", timeframe="1h")
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert second.context.diagnostics["cache_hit"] is True
    assert first.raw == second.raw


def test_legacy_analyzer_contract_kept_with_context_metadata():
    result = Analyzer().analyze(make_frame(), symbol="ETH", timeframe="4h")
    assert result["direction"] in {"LONG", "SHORT"}
    assert result["analysis_context"]["pipeline_version"] == "7.6"
    assert result["analysis_context"]["symbol"] == "ETH"
    assert "trade_dna_foundation" in result
