from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from services.copy_execution_journal import CopyExecutionJournal


@dataclass(frozen=True)
class ExecutionInspection:
    journal: dict[str, Any]
    plan: dict[str, Any]
    timeline: tuple[dict[str, Any], ...]


class ExecutionInspectionService:
    """Read-only execution observability facade used by Telegram and tests."""

    def __init__(self, journal: CopyExecutionJournal | None = None) -> None:
        self.journal = journal or CopyExecutionJournal()

    def recent(self, telegram_id: int, limit: int = 10) -> list[ExecutionInspection]:
        return [self._build(row) for row in self.journal.recent(telegram_id, limit=limit)]

    def get(self, telegram_id: int, reference: str) -> ExecutionInspection | None:
        row = self.journal.get_for_user(telegram_id, reference)
        return self._build(row) if row else None

    def fills(self, telegram_id: int, limit: int = 20) -> list[dict[str, Any]]:
        return self.journal.transition_events(
            telegram_id=telegram_id,
            limit=limit,
            statuses=("EXECUTED",),
        )

    def timeline(self, telegram_id: int, reference: str, limit: int = 50) -> list[dict[str, Any]]:
        row = self.journal.get_for_user(telegram_id, reference)
        if not row:
            return []
        return self.journal.transition_events(
            telegram_id=telegram_id,
            idempotency_key=str(row["idempotency_key"]),
            limit=limit,
        )

    def _build(self, row: dict[str, Any]) -> ExecutionInspection:
        try:
            plan = json.loads(str(row.get("plan_json") or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            plan = {}
        timeline = tuple(
            self.journal.transition_events(
                telegram_id=int(row["telegram_id"]),
                idempotency_key=str(row["idempotency_key"]),
                limit=50,
            )
        )
        return ExecutionInspection(journal=row, plan=plan, timeline=timeline)
