from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Generic, Mapping, TypeVar


class PipelineStage(str, Enum):
    RECEIVED = "RECEIVED"
    RESERVED = "RESERVED"
    REJECTED = "REJECTED"
    CLAIMED = "CLAIMED"
    DISPATCHED = "DISPATCHED"
    ORDERED = "ORDERED"
    FILLED = "FILLED"
    POSITIONED = "POSITIONED"
    RETRY_WAIT = "RETRY_WAIT"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"
    DEAD_LETTER = "DEAD_LETTER"
    CANCELLED = "CANCELLED"


TERMINAL_PIPELINE_STAGES = frozenset({
    PipelineStage.REJECTED,
    PipelineStage.EXECUTED,
    PipelineStage.FAILED,
    PipelineStage.DEAD_LETTER,
    PipelineStage.CANCELLED,
})

ALLOWED_PIPELINE_TRANSITIONS: Mapping[PipelineStage, frozenset[PipelineStage]] = {
    PipelineStage.RECEIVED: frozenset({PipelineStage.RESERVED, PipelineStage.REJECTED}),
    PipelineStage.RESERVED: frozenset({PipelineStage.CLAIMED, PipelineStage.REJECTED, PipelineStage.FAILED}),
    PipelineStage.CLAIMED: frozenset({PipelineStage.DISPATCHED, PipelineStage.RETRY_WAIT, PipelineStage.FAILED, PipelineStage.DEAD_LETTER}),
    PipelineStage.DISPATCHED: frozenset({PipelineStage.ORDERED, PipelineStage.RETRY_WAIT, PipelineStage.FAILED, PipelineStage.DEAD_LETTER}),
    PipelineStage.ORDERED: frozenset({PipelineStage.FILLED, PipelineStage.RETRY_WAIT, PipelineStage.FAILED, PipelineStage.CANCELLED}),
    PipelineStage.FILLED: frozenset({PipelineStage.POSITIONED, PipelineStage.FAILED}),
    PipelineStage.POSITIONED: frozenset({PipelineStage.EXECUTED, PipelineStage.FAILED}),
    PipelineStage.RETRY_WAIT: frozenset({PipelineStage.CLAIMED, PipelineStage.DEAD_LETTER, PipelineStage.CANCELLED}),
}


class InvalidPipelineTransition(ValueError):
    def __init__(self, current: PipelineStage, target: PipelineStage) -> None:
        self.current = current
        self.target = target
        super().__init__(f"Invalid execution pipeline transition: {current.value} -> {target.value}")


def can_transition_pipeline(current: PipelineStage, target: PipelineStage) -> bool:
    return current is target or target in ALLOWED_PIPELINE_TRANSITIONS.get(current, frozenset())


T = TypeVar("T")


@dataclass(frozen=True)
class StateTransition(Generic[T]):
    current: T
    target: T
    actor: str
    reason_code: str
    reason: str | None = None


class UnifiedStateMachine:
    """Central transition validator for pipeline and domain state machines."""

    @staticmethod
    def transition_pipeline(
        current: PipelineStage,
        target: PipelineStage,
        *,
        actor: str,
        reason_code: str,
        reason: str | None = None,
    ) -> StateTransition[PipelineStage]:
        if not can_transition_pipeline(current, target):
            raise InvalidPipelineTransition(current, target)
        return StateTransition(current=current, target=target, actor=actor, reason_code=reason_code, reason=reason)

    @staticmethod
    def validate(current: T, target: T, allowed: Mapping[T, frozenset[T]]) -> StateTransition[T]:
        if current != target and target not in allowed.get(current, frozenset()):
            raise ValueError(f"Invalid state transition: {current} -> {target}")
        return StateTransition(current=current, target=target, actor="engine", reason_code=str(target))
