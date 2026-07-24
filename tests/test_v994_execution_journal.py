from database.database import create_tables
from services.copy_execution_journal import CopyExecutionJournal, JournalStatus
from services.copy_execution_planner import CopyExecutionPlanner
from services.execution_models import RiskProfile


def signal():
    return {"id": 994, "symbol": "BTCUSDT", "timeframe": "1h", "side": "LONG", "status": "ACTIVE", "entry": 100,
            "current_price": 100, "stop": 98, "tp1": 104, "tp2": 106, "tp3": 108, "confidence": 80, "preferred_entry_low": 99, "preferred_entry_high": 101}


def test_journal_status_contract():
    assert JournalStatus.PLANNED.value == "PLANNED"
    assert JournalStatus.EXECUTING.value == "EXECUTING"
    assert JournalStatus.EXECUTED.value == "EXECUTED"


def test_reserve_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr("database.database.DATA_DIR", tmp_path)
    monkeypatch.setattr("database.database.DATABASE_NAME", tmp_path / "database.db")
    create_tables()
    plan = CopyExecutionPlanner().build(telegram_id=1, signal=signal(), profile=RiskProfile(max_notional_pct=100), balance=10000)
    journal = CopyExecutionJournal()
    first, created1 = journal.reserve(plan)
    second, created2 = journal.reserve(plan)
    assert created1 is True
    assert created2 is False
    assert first["id"] == second["id"]
    assert second["status"] == "PLANNED"


def test_journal_transitions_and_attempts(tmp_path, monkeypatch):
    monkeypatch.setattr("database.database.DATA_DIR", tmp_path)
    monkeypatch.setattr("database.database.DATABASE_NAME", tmp_path / "database.db")
    create_tables()
    plan = CopyExecutionPlanner().build(telegram_id=2, signal=signal(), profile=RiskProfile(max_notional_pct=100), balance=10000)
    journal = CopyExecutionJournal(); journal.reserve(plan)
    executing, claimed1 = journal.claim(plan.idempotency_key)
    duplicate, claimed2 = journal.claim(plan.idempotency_key)
    executed = journal.transition(plan.idempotency_key, JournalStatus.EXECUTED, execution_ref="paper:2:994")
    assert claimed1 is True
    assert claimed2 is False
    assert executing["attempt_count"] == 1
    assert duplicate["attempt_count"] == 1
    assert executed["status"] == "EXECUTED"
    assert executed["execution_ref"] == "paper:2:994"
