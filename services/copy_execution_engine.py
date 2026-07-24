from __future__ import annotations

from dataclasses import dataclass

from services.copy_execution_journal import CopyExecutionJournal, JournalStatus
from services.execution_adapter import ExecutionAdapter, PaperExecutionAdapter
from services.execution_models import CopyExecutionPlan, ExecutionMode


@dataclass(frozen=True)
class CopyExecutionResult:
    status: JournalStatus
    code: str
    reason: str
    execution_ref: str | None
    idempotency_key: str
    created: bool
    claimed: bool


class CopyExecutionEngine:
    """Idempotent paper execution coordinator.

    The engine owns the Planner -> Journal -> Adapter boundary. It reserves every
    plan, atomically claims approved work, invokes exactly one adapter, and writes
    the terminal result back to the journal. LIVE remains fail-closed.
    """

    TERMINAL = {
        JournalStatus.REJECTED,
        JournalStatus.EXECUTED,
        JournalStatus.FAILED,
        JournalStatus.CANCELLED,
    }

    def __init__(
        self,
        *,
        journal: CopyExecutionJournal | None = None,
        adapter: ExecutionAdapter | None = None,
        mode: ExecutionMode = ExecutionMode.PAPER,
    ) -> None:
        self.journal = journal or CopyExecutionJournal()
        self.adapter = adapter or PaperExecutionAdapter()
        self.mode = mode

    def execute(self, plan: CopyExecutionPlan) -> CopyExecutionResult:
        row, created = self.journal.reserve(plan)
        current = JournalStatus(row["status"])

        if not plan.approved:
            code = plan.code if created else "IDEMPOTENT_REPLAY"
            return self._from_row(row, created=created, claimed=False, code=code)

        if current in self.TERMINAL:
            return self._from_row(row, created=created, claimed=False, code="IDEMPOTENT_REPLAY")

        if self.mode is not ExecutionMode.PAPER or self.adapter.mode is not ExecutionMode.PAPER:
            failed = self.journal.transition(
                plan.idempotency_key,
                JournalStatus.FAILED,
                error="LIVE execution is disabled",
            )
            return self._from_row(failed, created=created, claimed=False, code="LIVE_DISABLED")

        claimed_row, claimed = self.journal.claim(plan.idempotency_key)
        if not claimed:
            return self._from_row(claimed_row, created=created, claimed=False, code="ALREADY_CLAIMED")

        try:
            adapter_result = self.adapter.execute(plan)
        except Exception as exc:  # adapter failures must always become journal state
            failed = self.journal.transition(
                plan.idempotency_key,
                JournalStatus.FAILED,
                error=f"{type(exc).__name__}: {exc}",
            )
            return self._from_row(failed, created=created, claimed=True, code="ADAPTER_EXCEPTION")

        if adapter_result.success:
            executed = self.journal.transition(
                plan.idempotency_key,
                JournalStatus.EXECUTED,
                execution_ref=adapter_result.execution_ref,
            )
            return self._from_row(executed, created=created, claimed=True, code=adapter_result.code)

        failed = self.journal.transition(
            plan.idempotency_key,
            JournalStatus.FAILED,
            error=f"{adapter_result.code}: {adapter_result.reason}",
            execution_ref=adapter_result.execution_ref,
        )
        return self._from_row(failed, created=created, claimed=True, code=adapter_result.code)

    @staticmethod
    def _from_row(
        row: dict,
        *,
        created: bool,
        claimed: bool,
        code: str,
    ) -> CopyExecutionResult:
        status = JournalStatus(row["status"])
        reason = row.get("last_error") or row.get("reason") or status.value
        return CopyExecutionResult(
            status=status,
            code=code,
            reason=str(reason),
            execution_ref=row.get("execution_ref"),
            idempotency_key=str(row["idempotency_key"]),
            created=created,
            claimed=claimed,
        )
