from __future__ import annotations

import pytest

from services.execution_adapter import PaperExecutionAdapter
from services.execution_context import ExecutionContext
from services.execution_dispatcher import ExecutionDispatcher
from services.execution_models import CopyExecutionPlan, ExecutionMode, ExecutionPlanStatus
from services.unified_execution_state import (
    InvalidPipelineTransition,
    PipelineStage,
    UnifiedStateMachine,
)


def plan(*, approved: bool = True) -> CopyExecutionPlan:
    return CopyExecutionPlan(
        plan_id="plan-build-1",
        idempotency_key="idem-build-1",
        status=ExecutionPlanStatus.APPROVED if approved else ExecutionPlanStatus.REJECTED,
        code="APPROVED" if approved else "LOW_CONFIDENCE",
        reason="ok" if approved else "below threshold",
        telegram_id=42,
        signal_id=99,
        exchange_account_id=None,
        symbol="BTC",
        timeframe="1h",
        side="LONG",
        order_type="MARKET",
        entry_price=65000.0,
        quantity=0.01 if approved else None,
        notional=650.0 if approved else None,
        leverage=5,
    )


def test_execution_context_is_immutable_and_enrichable():
    ctx = ExecutionContext.from_plan(plan(), worker_id="worker-a")
    updated = ctx.merge_metadata(stage="reserved").with_order({"id": 7})
    assert ctx.order is None
    assert updated.order == {"id": 7}
    assert updated.metadata["stage"] == "reserved"
    assert updated.idempotency_key == "idem-build-1"


def test_dispatcher_uses_context_aware_adapter():
    ctx = ExecutionContext.from_plan(plan(), mode=ExecutionMode.PAPER)
    result = ExecutionDispatcher().dispatch(ctx, PaperExecutionAdapter())
    assert result.adapter_result.success is True
    assert result.adapter_result.execution_ref
    assert result.context.metadata["dispatch_success"] is True


def test_dispatcher_keeps_live_fail_closed():
    ctx = ExecutionContext.from_plan(plan(), mode=ExecutionMode.LIVE)
    result = ExecutionDispatcher().dispatch(ctx, PaperExecutionAdapter())
    assert result.adapter_result.success is False
    assert result.adapter_result.code == "LIVE_DISABLED"


def test_unified_pipeline_state_machine_rejects_skips():
    valid = UnifiedStateMachine.transition_pipeline(
        PipelineStage.RECEIVED,
        PipelineStage.RESERVED,
        actor="engine",
        reason_code="RESERVE",
    )
    assert valid.target is PipelineStage.RESERVED
    with pytest.raises(InvalidPipelineTransition):
        UnifiedStateMachine.transition_pipeline(
            PipelineStage.RECEIVED,
            PipelineStage.EXECUTED,
            actor="engine",
            reason_code="SKIP",
        )
