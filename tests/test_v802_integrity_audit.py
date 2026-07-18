from __future__ import annotations

import importlib


def _fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("REQUIRE_PERSISTENT_DB", "false")
    import database.database as db
    import database.signal_history as sh
    import database.candidate_history as ch
    importlib.reload(db)
    importlib.reload(sh)
    importlib.reload(ch)
    db.create_tables()
    return db, sh.SignalHistory(), ch.CandidateHistory()


def _insert_signal(db, *, symbol: str, status: str, realized_r=None, result=None):
    now = "2026-07-18T12:00:00+00:00"
    with db.connect() as conn:
        cur = conn.execute(
            """INSERT INTO signals(
                owner_telegram_id,notification_chat_id,symbol,timeframe,side,status,
                created_at,updated_at,activated_at,closed_at,entry,stop,tp1,tp2,tp3,
                rr,confidence,bull_score,bear_score,recommendation,setup_key,features_json,reasons_json,
                realized_r,result,current_price
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                1,1,symbol,"1H","LONG",status,now,now,
                now if status != "WATCHING" else None,
                now if status in {"MANUAL_STOP","STOP","TP3","BREAKEVEN","INVALIDATED"} else None,
                100.0,90.0,110.0,120.0,130.0,2.0,60.0,60.0,40.0,
                "TEST","test-key-"+symbol,"{}","[]",realized_r,result,100.0,
            ),
        )
        return int(cur.lastrowid)


def test_resolved_win_rate_excludes_unclassified(tmp_path, monkeypatch):
    db, history, _ = _fresh_db(tmp_path, monkeypatch)
    _insert_signal(db, symbol="BTC", status="MANUAL_STOP", realized_r=1.0, result="MANUAL_STOP")
    _insert_signal(db, symbol="ETH", status="MANUAL_STOP", realized_r=-1.0, result="MANUAL_STOP")
    _insert_signal(db, symbol="SOL", status="MANUAL_STOP", realized_r=None, result="MANUAL_STOP")
    stats = history.get_stats(1)
    assert stats["closed_count"] == 2
    assert stats["wins"] == 1
    assert stats["losses"] == 1
    assert stats["unclassified_count"] == 0
    assert stats["win_rate"] == 50.0


def test_candidate_with_active_blocker_remains_pending(tmp_path, monkeypatch):
    db, _, candidates = _fresh_db(tmp_path, monkeypatch)
    blocker_id = _insert_signal(db, symbol="BTC", status="ACTIVE")
    candidates.upsert(owner_telegram_id=1, notification_chat_id=1, symbol="BTC", timeframe="1H",
        side="SHORT", observation_id=None, blocked_by_signal_id=blocker_id, snapshot={})
    rows = candidates.recent(1)
    assert len(rows) == 1
    assert rows[0]["blocked_by_signal_id"] == blocker_id


def test_candidate_with_closed_blocker_is_reconciled(tmp_path, monkeypatch):
    db, _, candidates = _fresh_db(tmp_path, monkeypatch)
    blocker_id = _insert_signal(db, symbol="BTC", status="MANUAL_STOP", realized_r=0.5, result="MANUAL_STOP")
    candidates.upsert(owner_telegram_id=1, notification_chat_id=1, symbol="BTC", timeframe="1H",
        side="SHORT", observation_id=None, blocked_by_signal_id=blocker_id, snapshot={})
    assert candidates.recent(1) == []
