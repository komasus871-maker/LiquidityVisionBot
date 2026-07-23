import json
from services.copy_similarity import CopySimilarityService, StrategyGenomeBuilder
from version import APP_VERSION

def test_release_version():
    assert APP_VERSION == "9.8.0"

def test_genome_is_deterministic_and_contextual():
    signal = {"symbol":"BTCUSDT","side":"LONG","timeframe":"1h","setup_key":"sweep_fvg","confidence":78,"rr":2.8,
              "features_json":json.dumps({"bos":True,"choch":False,"market_regime":"trend","rsi":57}),
              "intelligence_json":json.dumps({"session":"London","atr_pct":1.4})}
    builder = StrategyGenomeBuilder(); first = builder.build(signal); second = builder.build(dict(reversed(list(signal.items()))))
    assert first == second
    assert builder.fingerprint(first) == builder.fingerprint(second)
    assert first["bos"] is True and first["market_regime"] == "trend" and first["session"] == "london"

def test_similarity_rewards_full_context_not_symbol_only():
    service = CopySimilarityService()
    base = {"symbol":"BTCUSDT","side":"LONG","timeframe":"1h","setup_key":"sweep_fvg","market_regime":"trend","bos":True,"choch":False,"rsi":55,"confidence":80}
    same_context_other_symbol = {**base,"symbol":"ETHUSDT","rsi":58}
    same_symbol_wrong_context = {**base,"side":"SHORT","timeframe":"5m","setup_key":"breakdown","bos":False}
    assert service.similarity(base, same_context_other_symbol) > service.similarity(base, same_symbol_wrong_context)

def test_similarity_summary_metrics(monkeypatch):
    service = CopySimilarityService()
    monkeypatch.setattr(service, "_history", lambda *_args, **_kwargs: [
        {"signal_id":2,"symbol":"ETHUSDT","timeframe":"1h","side":"LONG","status":"CLOSED","realized_r":2.0,"close_reason":"TP3","max_profit_pct":4.0,"max_drawdown_pct":-0.5,"genome_json":json.dumps({"side":"LONG","timeframe":"1h","setup_key":"sweep","bos":True})},
        {"signal_id":3,"symbol":"SOLUSDT","timeframe":"1h","side":"LONG","status":"REJECTED","shadow_realized_r":-1.0,"shadow_result":"STOP","max_profit_pct":0.4,"max_drawdown_pct":-1.2,"genome_json":json.dumps({"side":"LONG","timeframe":"1h","setup_key":"sweep","bos":True})},
    ])
    report = service.report(7, {"id":1,"symbol":"BTCUSDT","side":"LONG","timeframe":"1h","setup_key":"sweep","bos":True})
    assert report["found"] == 2 and report["win_rate"] == 50.0 and report["average_r"] == 0.5
    assert {item.source for item in report["matches"]} == {"EXECUTED","SHADOW"}
