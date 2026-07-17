from services.similarity_engine_v2 import SimilarityEngineV2, SimilarTrade
from services.replay_renderer import render_intelligence


def test_best_and_worst_match_are_similarity_based():
    cases = [
        SimilarTrade(1, "BTCUSDT", "1h", "LONG", "TP3", 50.1, 3.0, 5.0, -1.0, ["a"], ["b"]),
        SimilarTrade(2, "ETHUSDT", "1h", "LONG", "STOP", 50.7, -1.0, 1.0, -2.0, ["a"], ["c"]),
    ]
    result = SimilarityEngineV2.summarize(cases)
    assert result["best_match"]["similarity"] == 50.7
    assert result["worst_match"]["similarity"] == 50.1
    assert result["best_trade"]["realized_r"] == 3.0


def test_replay_renderer_exposes_memory_and_dna():
    cards = render_intelligence({}, {
        "dna": {"fingerprint": "abc", "market_regime": "TREND", "entry_quality": 80, "risk_quality": 70, "readiness": 75},
        "memory": {"what_worked": ["Entry"], "what_failed": ["Exit"], "strengths": ["Trend"], "weaknesses": ["Volume"], "lesson": "Protect profit"},
        "similarity": {}, "historical": {}, "similar_trades": [],
    })
    text = "\n".join(cards)
    assert "Trade DNA" in text
    assert "What worked" in text
    assert "Protect profit" in text
