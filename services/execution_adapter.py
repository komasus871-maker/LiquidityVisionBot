from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from services.execution_models import CopyExecutionPlan, ExecutionMode


@dataclass(frozen=True)
class ExecutionAdapterResult:
    success: bool
    execution_ref: str | None = None
    code: str = "EXECUTED"
    reason: str = "Execution completed"


class ExecutionAdapter(Protocol):
    """Write boundary used by CopyExecutionEngine.

    Adapters must explicitly declare their mode. v9.9.5a only ships a PAPER
    adapter; any LIVE-capable adapter remains unsupported and fail-closed.
    """

    mode: ExecutionMode

    def execute(self, plan: CopyExecutionPlan) -> ExecutionAdapterResult:
        ...


class PaperExecutionAdapter:
    mode = ExecutionMode.PAPER

    def execute(self, plan: CopyExecutionPlan) -> ExecutionAdapterResult:
        if not plan.approved:
            return ExecutionAdapterResult(False, code="PLAN_REJECTED", reason=plan.reason)
        if plan.quantity is None or plan.quantity <= 0:
            return ExecutionAdapterResult(False, code="INVALID_QUANTITY", reason="Approved plan has no positive quantity")
        execution_ref = f"paper:{plan.telegram_id}:{plan.signal_id}:{plan.plan_id}"
        return ExecutionAdapterResult(True, execution_ref=execution_ref)
