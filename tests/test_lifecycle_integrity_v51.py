from __future__ import annotations

import os
from pathlib import Path


def test_plan_updates_stop_after_trigger(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.delenv('REQUIRE_PERSISTENT_DB', raising=False)

    import importlib
    import database.database as db
    import database.signal_history as sh
    importlib.reload(db)
    importlib.reload(sh)
    db.create_tables()
    history = sh.SignalHistory()
    signal = {
        'owner_telegram_id': 1, 'notification_chat_id': 1,
        'symbol': 'BTC-USDT-SWAP', 'timeframe': '1h', 'side': 'LONG', 'status': 'WATCHING',
        'entry': 100.0, 'preferred_entry_low': 99.0, 'preferred_entry_high': 101.0,
        'stop': 95.0, 'tp1': 105.0, 'tp2': 110.0, 'tp3': 115.0, 'rr': 3.0,
        'confidence': 60.0, 'bull_score': 60.0, 'bear_score': 20.0,
        'recommendation': 'WAIT', 'setup_key': 'test', 'features': {}, 'reasons': [],
    }
    signal_id = history.save(signal)
    history.refresh_duplicate(signal_id, {**signal, 'entry': 99.5})
    assert history.get_by_id(signal_id)['entry'] == 99.5
    assert [x['event_type'] for x in history.get_events(signal_id)] == ['CREATED', 'PLAN_UPDATED']

    history.update_lifecycle(signal_id, status='TRIGGERED', plan_locked_at='now')
    history.refresh_duplicate(signal_id, {**signal, 'entry': 98.0})
    assert history.get_by_id(signal_id)['entry'] == 99.5


def test_scenario_engine_has_two_paths():
    from services.scenario_engine import ScenarioEngine
    result = ScenarioEngine.build({
        'direction': 'LONG',
        'execution_status': '🎯 WAIT FOR PULLBACK',
        'preferred_entry_low': 90,
        'preferred_entry_high': 95,
        'triggers': ['Wait for BOS'],
        'alternative_conditions': ['Bearish CHOCH'],
    })
    assert len(result['primary']) >= 3
    assert result['alternative'][0] == 'Bearish CHOCH'
