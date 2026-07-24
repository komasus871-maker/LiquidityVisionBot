from __future__ import annotations

from dataclasses import dataclass

from services.copy_execution_journal import CopyExecutionJournal, JournalStatus
from services.execution_adapter import ExecutionAdapter, PaperExecutionAdapter
from services.execution_models import CopyExecutionPlan, ExecutionMode
from services.paper_execution_lifecycle import PaperExecutionLifecycle
from services.execution_reliability import ExecutionRetryPolicy


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
        JournalStatus.DEAD_LETTER,
        JournalStatus.CANCELLED,
    }

    def __init__(
        self,
        *,
        journal: CopyExecutionJournal | None = None,
        adapter: ExecutionAdapter | None = None,
        mode: ExecutionMode = ExecutionMode.PAPER,
        lifecycle: PaperExecutionLifecycle | None = None,
        retry_policy: ExecutionRetryPolicy | None = None,
        worker_id: str = "copy-worker",
        lease_seconds: int = 180,
    ) -> None:
        self.journal = journal or CopyExecutionJournal()
        self.adapter = adapter or PaperExecutionAdapter()
        self.mode = mode
        self.lifecycle = lifecycle or PaperExecutionLifecycle()
        self.retry_policy = retry_policy or ExecutionRetryPolicy()
        self.worker_id = worker_id
        self.lease_seconds = max(30, int(lease_seconds))

    def execute(self, plan: CopyExecutionPlan) -> CopyExecutionResult:
        row, created = self.journal.reserve(plan)
        current = JournalStatus(row["status"])

        if not plan.approved:
            self.lifecycle.reject(plan, reason_code=plan.code, reason=plan.reason)
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

        claimed_row, claimed = self.journal.claim(
            plan.idempotency_key, worker_id=self.worker_id, lease_seconds=self.lease_seconds
        )
        if not claimed:
            return self._from_row(claimed_row, created=created, claimed=False, code="ALREADY_CLAIMED")

        try:
            adapter_result = self.adapter.execute(plan)
        except Exception as exc:  # transient adapter failures are retried durably
            error = f"{type(exc).__name__}: {exc}"
            attempt_count = int(claimed_row.get("attempt_count") or 0)
            max_attempts = int(claimed_row.get("max_attempts") or 5)
            decision = self.retry_policy.decide(
                code="ADAPTER_EXCEPTION", attempt_count=attempt_count, max_attempts=max_attempts
            )
            if decision.retryable:
                retried = self.journal.schedule_retry(
                    plan.idempotency_key, error=error, code=decision.code,
                    next_attempt_at=self.retry_policy.due_at(decision.delay_seconds),
                )
                return self._from_row(retried, created=created, claimed=True, code=decision.code)
            dead = self.journal.dead_letter(plan.idempotency_key, error=error, code=decision.code)
            return self._from_row(dead, created=created, claimed=True, code=decision.code)

        if adapter_result.success:
            execution_ref = adapter_result.execution_ref or f"paper:{plan.telegram_id}:{plan.signal_id}:{plan.plan_id}"
            self.lifecycle.execute_market(
                plan,
                fill_price=float(plan.entry_price or 0.0),
                execution_ref=execution_ref,
                slippage_pct=plan.expected_slippage_pct,
            )
            executed = self.journal.transition(
                plan.idempotency_key,
                JournalStatus.EXECUTED,
                execution_ref=execution_ref,
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
