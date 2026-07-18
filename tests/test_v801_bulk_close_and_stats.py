from __future__ import annotations

import importlib


def _repo(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("REQUIRE_PERSISTENT_DB", "false")
    import database.database as db
    import database.signal_history as sh
    importlib.reload(db)
    importlib.reload(sh)
    db.create_tables()
    return sh.SignalHistory()


def _signal(repo, owner, symbol, status, current, activated=True):
    sid = repo.save({
        "owner_telegram_id": owner, "notification_chat_id": owner, "symbol": symbol, "timeframe": "1h",
        "side": "LONG", "status": "WATCHING", "entry": 100.0, "preferred_entry_low": 99.0,
        "preferred_entry_high": 100.0, "stop": 95.0, "tp1": 105.0, "tp2": 110.0,
        "tp3": 115.0, "rr": 3.0, "confidence": 70.0, "bull_score": 70.0,
        "bear_score": 30.0, "recommendation": "BUY", "setup_key": symbol, "features": {}, "reasons": [],
    })
    fields = {"status": status, "current_price": current}
    if activated:
        fields["activated_at"] = "2026-07-18T00:00:00+00:00"
    repo.update_lifecycle(sid, **fields)
    return sid


def test_close_all_only_closes_activated_positions(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    active = _signal(repo, 7, "BTC", "ACTIVE", 105.0)
    tp1 = _signal(repo, 7, "ETH", "TP1", 110.0)
    watching = _signal(repo, 7, "SOL", "WATCHING", 120.0, activated=False)
    other = _signal(repo, 8, "XRP", "ACTIVE", 105.0)

    closed = repo.manual_stop_all(7)
    assert {x["id"] for x in closed} == {active, tp1}
    assert repo.get_by_id(active)["status"] == "MANUAL_STOP"
    assert repo.get_by_id(tp1)["status"] == "MANUAL_STOP"
    assert repo.get_by_id(watching)["status"] == "WATCHING"
    assert repo.get_by_id(other)["status"] == "ACTIVE"


def test_manual_cancel_has_no_fake_r_and_closed_stats_are_outcome_based(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    waiting = _signal(repo, 7, "HYPE", "WATCHING", 80.0, activated=False)
    cancelled = repo.manual_stop(waiting, 7)
    assert cancelled["result"] == "MANUAL_CANCEL"
    assert cancelled["realized_r"] is None

    win = _signal(repo, 7, "BTC", "ACTIVE", 105.0)
    loss = _signal(repo, 7, "ETH", "ACTIVE", 97.5)
    repo.manual_stop(win, 7)
    repo.manual_stop(loss, 7)
    stats = repo.get_stats(7)
    assert stats["closed_count"] == 2
    assert stats["wins"] == 1
    assert stats["losses"] == 1
    assert stats["win_rate"] == 50.0
    assert stats["manual_close_count"] == 2
    assert stats["invalidated_count"] == 1
