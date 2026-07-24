from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _fresh(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import database.database as db
    import services.copy_execution_journal as journal
    import services.copy_execution_engine as engine
    import services.execution_reliability as reliability
    importlib.reload(db)
    db.create_tables()
    importlib.reload(journal)
    importlib.reload(reliability)
    importlib.reload(engine)
    return db, journal, engine, reliability


def _plan(key: str = "retry-test"):
    from services.execution_models import CopyExecutionPlan, ExecutionPlanStatus
    return CopyExecutionPlan(
        plan_id=key, idempotency_key=key, telegram_id=1, signal_id=10,
        exchange_account_id=None, symbol="BTC", timeframe="1h", side="LONG",
        status=ExecutionPlanStatus.APPROVED, code="APPROVED", reason="ok",
        order_type="MARKET", quantity=0.01, notional=100.0, entry_price=10000.0,
        stop_loss=9900.0, take_profits=(10100.0,), leverage=1,
        expected_slippage_pct=0.1,
    )


def test_transient_failure_enters_retry_wait(tmp_path, monkeypatch):
    _, journal_mod, engine_mod, reliability = _fresh(tmp_path, monkeypatch)
    from services.execution_adapter import ExecutionAdapter
    from services.execution_models import ExecutionMode

    class FailingAdapter(ExecutionAdapter):
        mode = ExecutionMode.PAPER
        def execute(self, plan):
            raise TimeoutError("temporary timeout")

    journal = journal_mod.CopyExecutionJournal()
    engine = engine_mod.CopyExecutionEngine(
        journal=journal, adapter=FailingAdapter(),
        retry_policy=reliability.ExecutionRetryPolicy(base_seconds=1),
    )
    result = engine.execute(_plan())
    assert result.status.value == "RETRY_WAIT"
    row = journal.get("retry-test")
    assert row["next_attempt_at"]
    assert row["claimed_by"] is None


def test_expired_lease_is_recovered(tmp_path, monkeypatch):
    db, journal_mod, _, _ = _fresh(tmp_path, monkeypatch)
    journal = journal_mod.CopyExecutionJournal()
    journal.reserve(_plan("lease-test"))
    row, claimed = journal.claim("lease-test", worker_id="worker-a", lease_seconds=30)
    assert claimed and row["status"] == "EXECUTING"
    expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with db.connect() as conn:
        conn.execute("UPDATE copy_execution_journal SET lease_expires_at=? WHERE idempotency_key=?", (expired, "lease-test"))
    result = journal.recover_expired_claims()
    assert result["recovered"] == 1
    assert journal.get("lease-test")["status"] == "RETRY_WAIT"


def test_max_attempts_goes_dead_letter(tmp_path, monkeypatch):
    db, journal_mod, _, _ = _fresh(tmp_path, monkeypatch)
    journal = journal_mod.CopyExecutionJournal()
    journal.reserve(_plan("dead-test"))
    journal.claim("dead-test", worker_id="worker-a", lease_seconds=30)
    expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with db.connect() as conn:
        conn.execute("UPDATE copy_execution_journal SET attempt_count=5,max_attempts=5,lease_expires_at=? WHERE idempotency_key=?", (expired, "dead-test"))
    result = journal.recover_expired_claims()
    assert result["dead_lettered"] == 1
    assert journal.get("dead-test")["status"] == "DEAD_LETTER"
