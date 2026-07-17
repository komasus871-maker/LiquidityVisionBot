from domain.intelligence import TradeDNABuilder
from services.similarity_engine_v2 import SimilarityEngineV2
from services.trade_memory import TradeMemoryService


def sample(**overrides):
    base = {"direction":"LONG","trend":"Bullish","structure":"Bullish BOS","bos":"Confirmed",
            "choch":"None","liquidity":"Sell-side swept","sweep":"SSL sweep","order_block":"Bullish OB",
            "fvg":"Bullish FVG","premium":{"zone":"Discount"},"market_regime":{"code":"TRENDING"},
            "htf_alignment":"Aligned","rsi":56,"ema50":99,"ema200":95,"price":100,"atr":1.2,
            "entry":100,"stop":98,"tp1":103,"tp2":105,"tp3":108,"rr":2.5,"confidence":78,
            "direction_score":82,"entry_quality":76,"risk_quality":80,"execution_readiness":74,
            "ai_grade":"A","execution_status":"READY"}
    base.update(overrides)
    return base


def test_trade_dna_is_stable_and_rich():
    dna = TradeDNABuilder.build(sample(), symbol="BTCUSDT", timeframe="1h")
    assert dna.symbol == "BTCUSDT"
    assert dna.fingerprint
    assert dna.risk_pct == 2.0
    assert len(dna.tags) >= 5


def test_similarity_explains_matches_and_differences():
    a = TradeDNABuilder.build(sample(), symbol="BTCUSDT", timeframe="1h")
    b = TradeDNABuilder.build(sample(rsi=59, direction_score=79), symbol="ETHUSDT", timeframe="1h")
    score, matches, differences = SimilarityEngineV2.compare(a, b)
    assert score > 80
    assert matches
    c = TradeDNABuilder.build(sample(direction="SHORT", trend="Bearish", structure="Bearish CHOCH"), symbol="BTCUSDT", timeframe="1h")
    low, _, diffs = SimilarityEngineV2.compare(a, c)
    assert low < score
    assert diffs


def test_memory_lesson_is_deterministic():
    dna = TradeDNABuilder.build(sample(), symbol="BTCUSDT", timeframe="1h").to_dict()
    memory = TradeMemoryService._lessons({"status":"TP3","result":"TP3","realized_r":3,"max_profit_pct":5,"max_drawdown_pct":-1}, dna)
    assert memory["realized_r"] == 3
    assert memory["lesson"]
    assert memory["what_worked"]
