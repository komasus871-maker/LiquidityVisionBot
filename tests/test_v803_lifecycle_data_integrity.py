from __future__ import annotations

import importlib


def _fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("REQUIRE_PERSISTENT_DB", "false")
    import database.database as db
    import database.signal_history as sh
    import services.runtime_diagnostics as rd
    importlib.reload(db)
    importlib.reload(sh)
    importlib.reload(rd)
    db.create_tables()
    return db, sh.SignalHistory(), rd


def test_legacy_invalidated_without_realized_r_is_not_closed_trade(tmp_path, monkeypatch):
    db, history, _ = _fresh_db(tmp_path, monkeypatch)
    now = "2026-07-18T12:00:00+00:00"
    with db.connect() as conn:
        conn.execute("""INSERT INTO signals(
            owner_telegram_id,notification_chat_id,symbol,timeframe,side,status,
            created_at,updated_at,activated_at,closed_at,entry,stop,tp1,tp2,tp3,
            rr,confidence,bull_score,bear_score,recommendation,setup_key,features_json,reasons_json,
            realized_r,result,current_price
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            1,1,"KAT","15m","SHORT","INVALIDATED",now,now,now,now,
            1.0,1.1,0.9,0.8,0.7,2.0,60,40,60,"TEST","legacy","{}","[]",
            None,None,0.8,
        ))
    stats = history.get_stats(1)
    assert stats["closed_count"] == 0
    assert stats["unclassified_count"] == 0
    assert stats["activated_invalidated_count"] == 1


def test_manual_close_count_requires_realized_result(tmp_path, monkeypatch):
    db, history, _ = _fresh_db(tmp_path, monkeypatch)
    now = "2026-07-18T12:00:00+00:00"
    with db.connect() as conn:
        for result_r in (1.0, None):
            conn.execute("""INSERT INTO signals(
                owner_telegram_id,notification_chat_id,symbol,timeframe,side,status,
                created_at,updated_at,activated_at,closed_at,entry,stop,tp1,tp2,tp3,
                rr,confidence,bull_score,bear_score,recommendation,setup_key,features_json,reasons_json,
                realized_r,result,current_price
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                1,1,"BTC","1h","LONG","MANUAL_STOP",now,now,now,now,
                100,90,110,120,130,2,60,60,40,"TEST",f"m-{result_r}","{}","[]",
                result_r,"MANUAL_STOP",105,
            ))
    stats = history.get_stats(1)
    assert stats["manual_close_count"] == 1
    assert stats["closed_count"] == 1


def test_admin_version_and_watch_error_details(tmp_path, monkeypatch):
    db, _, rd = _fresh_db(tmp_path, monkeypatch)
    now = "2026-07-18T12:00:00+00:00"
    with db.connect() as conn:
        conn.execute("""INSERT INTO watch_states(
            telegram_id,symbol,timeframe,snapshot_json,updated_at,last_checked_at,last_error,consecutive_errors
        ) VALUES(?,?,?,?,?,?,?,?)""", (1,"KAT","15m","{}",now,now,"API timeout",2))
    report = rd.collect_runtime_diagnostics()
    assert report["version"] == "8.5.0"
    assert report["watch_errors"][0]["symbol"] == "KAT"
    assert report["watch_errors"][0]["last_error"] == "API timeout"
