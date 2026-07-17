import pandas as pd

from services.explainer import Explainer
from services.trade_intelligence import TradeIntelligenceEngine


def test_analyzer_keeps_direction_and_readiness_independent(monkeypatch):
    # Regression assertion on source-level output contract.
    source = open('services/analyzer.py', encoding='utf-8').read()
    assert 'data["confidence"] = round(direction_score, 1)' in source
    assert 'data["confidence"] = round(execution["readiness"], 1)' not in source


def test_confidence_is_smoothed_and_step_limited():
    engine = TradeIntelligenceEngine()
    df = pd.DataFrame({
        'open': [100, 100, 100, 100, 100, 100],
        'high': [101] * 6, 'low': [99] * 6,
        'close': [100, 100, 100, 100, 100, 100],
        'volume': [10] * 6,
    })
    signal = {'side': 'LONG', 'entry': 100, 'stop': 90, 'tp1': 110,
              'dynamic_confidence': 90, 'confidence': 90, 'features_json': '{}'}
    snap = engine.evaluate(signal, 100, df)
    assert abs(snap.confidence - 90) <= 10


def test_explainer_hides_unreliable_zero_probabilities():
    text = Explainer().build({
        'direction': 'LONG', 'reasons': [], 'triggers': [], 'price': 1,
        'preferred_entry_low': 0.9, 'preferred_entry_high': 1.0, 'stop': 0.8,
        'similar_stats': {'samples': 7, 'tp1_rate': 0, 'tp2_rate': 0, 'tp3_rate': 0,
                          'stop_rate': 14.3, 'reliability': 'Insufficient'},
        'unified_decision': {'score': 47.4, 'action': 'SKIP'},
    }, 'ONDO')
    assert 'No reliable probability estimate yet' in text
    assert 'TP1 0' not in text



def test_stop_guard_precedes_intelligence_evaluation():
    source = open('services/signal_tracker.py', encoding='utf-8').read()
    guard = source.index('if self._stop_hit(side, price, effective_stop):', source.index('signal.update(common)'))
    intelligence = source.index('snapshot = self.intelligence.evaluate(signal, price, df)')
    assert guard < intelligence
