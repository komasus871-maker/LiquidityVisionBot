from __future__ import annotations

import pytest

from database.database import create_tables
from services.copy_execution_journal import (
    CopyExecutionJournal,
    InvalidJournalTransition,
    JournalStatus,
    can_transition_journal_state,
)
from services.copy_execution_planner import CopyExecutionPlanner
from services.execution_models import RiskProfile
from version import APP_VERSION, RELEASE_NAME


def _signal(status: str = "ACTIVE") -> dict:
    return {
        "id": 9953,
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "side": "LONG",
        "status": status,
        "entry": 100.0,
        "current_price": 100.0,
        "stop": 98.0,
        "tp1": 104.0,
        "tp2": 106.0,
        "tp3": 108.0,
        "confidence": 80.0,
        "preferred_entry_low": 99.0,
        "preferred_entry_high": 101.0,
    }


def _journal_and_plan(tmp_path, monkeypatch):
    monkeypatch.setattr("database.database.DATA_DIR", tmp_path)
    monkeypatch.setattr("database.database.DATABASE_NAME", tmp_path / "database.db")
    create_tables()
    plan = CopyExecutionPlanner().build(
        telegram_id=53,
        signal=_signal(),
        profile=RiskProfile(max_notional_pct=100),
        balance=10_000,
    )
    journal = CopyExecutionJournal()
    journal.reserve(plan)
    return journal, plan


def test_release_identity() -> None:
    assert APP_VERSION == "9.9.5c"
    assert RELEASE_NAME == "Journal State Machine Integration"


def test_transition_table_exposes_expected_execution_path() -> None:
    assert can_transition_journal_state(JournalStatus.PLANNED, JournalStatus.EXECUTING)
    assert can_transition_journal_state(JournalStatus.EXECUTING, JournalStatus.EXECUTED)
    assert can_transition_journal_state(JournalStatus.PLANNED, JournalStatus.FAILED)
    assert not can_transition_journal_state(JournalStatus.PLANNED, JournalStatus.EXECUTED)
    assert not can_transition_journal_state(JournalStatus.EXECUTED, JournalStatus.FAILED)


def test_journal_rejects_skipping_execution_state(tmp_path, monkeypatch) -> None:
    journal, plan = _journal_and_plan(tmp_path, monkeypatch)

    with pytest.raises(InvalidJournalTransition) as exc:
        journal.transition(plan.idempotency_key, JournalStatus.EXECUTED)

    assert exc.value.current is JournalStatus.PLANNED
    assert exc.value.target is JournalStatus.EXECUTED
    assert journal.get(plan.idempotency_key)["status"] == JournalStatus.PLANNED.value


def test_terminal_execution_state_is_immutable(tmp_path, monkeypatch) -> None:
    journal, plan = _journal_and_plan(tmp_path, monkeypatch)
    _, claimed = journal.claim(plan.idempotency_key)
    assert claimed
    journal.transition(plan.idempotency_key, JournalStatus.EXECUTED, execution_ref="paper:53:9953")

    with pytest.raises(InvalidJournalTransition):
        journal.transition(plan.idempotency_key, JournalStatus.FAILED, error="late failure")

    row = journal.get(plan.idempotency_key)
    assert row["status"] == JournalStatus.EXECUTED.value
    assert row["execution_ref"] == "paper:53:9953"


def test_idempotent_same_state_preserves_existing_metadata(tmp_path, monkeypatch) -> None:
    journal, plan = _journal_and_plan(tmp_path, monkeypatch)
    journal.transition(plan.idempotency_key, JournalStatus.FAILED, error="LIVE execution is disabled")

    replay = journal.transition(plan.idempotency_key, JournalStatus.FAILED)

    assert replay["status"] == JournalStatus.FAILED.value
    assert replay["last_error"] == "LIVE execution is disabled"
