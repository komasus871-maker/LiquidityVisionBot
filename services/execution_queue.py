from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from services.copy_execution_engine import CopyExecutionEngine, CopyExecutionResult
from services.copy_execution_journal import CopyExecutionJournal, JournalStatus
from services.execution_models import CopyExecutionPlan, ExecutionPlanStatus


@dataclass(frozen=True)
class QueueEnqueueResult:
    row: dict[str, Any]
    created: bool


class ExecutionQueueService:
    """Persistent Planner -> Engine hand-off backed by the execution journal.

    PLANNED journal rows are the durable queue. This avoids a second queue table,
    preserves idempotency, and lets future recovery workers resume the same rows.
    """

    def __init__(
        self,
        *,
        journal: CopyExecutionJournal | None = None,
        engine: CopyExecutionEngine | None = None,
    ) -> None:
        self.journal = journal or CopyExecutionJournal()
        self.engine = engine or CopyExecutionEngine(journal=self.journal)

    def enqueue(self, plan: CopyExecutionPlan) -> QueueEnqueueResult:
        row, created = self.journal.reserve(plan)
        return QueueEnqueueResult(row=row, created=created)

    def process_next(self) -> CopyExecutionResult | None:
        rows = self.journal.pending(limit=1)
        if not rows:
            return None
        return self.engine.execute(self.plan_from_row(rows[0]))

    def drain(self, limit: int = 25) -> list[CopyExecutionResult]:
        results: list[CopyExecutionResult] = []
        for row in self.journal.pending(limit=limit):
            results.append(self.engine.execute(self.plan_from_row(row)))
        return results

    def summary(self, telegram_id: int) -> dict[str, int]:
        return self.journal.status_counts(telegram_id)

    def recent(self, telegram_id: int, limit: int = 10) -> list[dict[str, Any]]:
        return self.journal.recent(telegram_id, limit=limit)

    @staticmethod
    def plan_from_row(row: dict[str, Any]) -> CopyExecutionPlan:
        payload = json.loads(str(row["plan_json"]))
        payload["status"] = ExecutionPlanStatus(str(payload["status"]))
        payload["take_profits"] = tuple(payload.get("take_profits") or ())
        return CopyExecutionPlan(**payload)
