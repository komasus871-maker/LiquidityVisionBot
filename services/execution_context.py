from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Mapping

from services.execution_models import CopyExecutionPlan, ExecutionMode


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ExecutionContext:
    """Immutable carrier for the whole execution pipeline.

    Build 9.9.6.1 introduces a single object that can be enriched stage by stage
    without expanding method signatures. Existing plan-based APIs remain
    supported; ``from_plan`` is the compatibility bridge.
    """

    plan: CopyExecutionPlan
    mode: ExecutionMode = ExecutionMode.PAPER
    worker_id: str | None = None
    journal: Mapping[str, Any] | None = None
    order: Mapping[str, Any] | None = None
    fills: tuple[Mapping[str, Any], ...] = ()
    position: Mapping[str, Any] | None = None
    portfolio: Mapping[str, Any] | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    @classmethod
    def from_plan(
        cls,
        plan: CopyExecutionPlan,
        *,
        mode: ExecutionMode = ExecutionMode.PAPER,
        worker_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "ExecutionContext":
        return cls(plan=plan, mode=mode, worker_id=worker_id, metadata=dict(metadata or {}))

    @property
    def idempotency_key(self) -> str:
        return self.plan.idempotency_key

    @property
    def approved(self) -> bool:
        return self.plan.approved

    def evolve(self, **changes: Any) -> "ExecutionContext":
        changes.setdefault("updated_at", utc_now_iso())
        return replace(self, **changes)

    def with_journal(self, row: Mapping[str, Any] | None) -> "ExecutionContext":
        return self.evolve(journal=dict(row) if row is not None else None)

    def with_order(self, row: Mapping[str, Any] | None) -> "ExecutionContext":
        return self.evolve(order=dict(row) if row is not None else None)

    def add_fill(self, row: Mapping[str, Any] | None) -> "ExecutionContext":
        if row is None:
            return self
        return self.evolve(fills=(*self.fills, dict(row)))

    def with_position(self, row: Mapping[str, Any] | None) -> "ExecutionContext":
        return self.evolve(position=dict(row) if row is not None else None)

    def with_portfolio(self, row: Mapping[str, Any] | None) -> "ExecutionContext":
        return self.evolve(portfolio=dict(row) if row is not None else None)

    def merge_metadata(self, **values: Any) -> "ExecutionContext":
        merged = dict(self.metadata)
        merged.update(values)
        return self.evolve(metadata=merged)
