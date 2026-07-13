import os
from pathlib import Path


def test_candidate_history_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("REQUIRE_PERSISTENT_DB", "false")

    import importlib
    import database.database as db
    import database.candidate_history as ch

    importlib.reload(db)
    importlib.reload(ch)
    db.create_tables()

    repo = ch.CandidateHistory()
    cid = repo.upsert(
        owner_telegram_id=1,
        notification_chat_id=1,
        symbol="BTC",
        timeframe="1h",
        side="SHORT",
        observation_id=2,
        blocked_by_signal_id=7,
        snapshot={"direction_score": 71.0},
    )
    assert cid > 0
    rows = repo.recent(1)
    assert len(rows) == 1
    assert rows[0]["blocked_by_signal_id"] == 7
    repo.resolve_market(1, "BTC", "1h", promoted_signal_id=9)
    assert repo.recent(1) == []
