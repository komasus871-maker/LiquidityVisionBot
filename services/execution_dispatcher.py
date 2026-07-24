from __future__ import annotations

from dataclasses import dataclass

from services.execution_adapter import ExecutionAdapter, ExecutionAdapterResult
from services.execution_context import ExecutionContext
from services.execution_models import ExecutionMode


@dataclass(frozen=True)
class DispatchResult:
    context: ExecutionContext
    adapter_result: ExecutionAdapterResult


class ExecutionDispatcher:
    """Single adapter dispatch boundary introduced in Build 9.9.6.3.

    It accepts an ExecutionContext while preserving adapters that still implement
    the legacy ``execute(plan)`` protocol. This lets services migrate gradually
    without a flag day refactor.
    """

    def dispatch(self, context: ExecutionContext, adapter: ExecutionAdapter) -> DispatchResult:
        if context.mode is not ExecutionMode.PAPER or adapter.mode is not ExecutionMode.PAPER:
            result = ExecutionAdapterResult(
                success=False,
                code="LIVE_DISABLED",
                reason="LIVE execution is disabled",
            )
            return DispatchResult(context=context.merge_metadata(dispatch_code=result.code), adapter_result=result)

        execute_context = getattr(adapter, "execute_context", None)
        if callable(execute_context):
            result = execute_context(context)
        else:
            result = adapter.execute(context.plan)
        enriched = context.merge_metadata(
            dispatch_code=result.code,
            dispatch_success=result.success,
            execution_ref=result.execution_ref,
        )
        return DispatchResult(context=enriched, adapter_result=result)
