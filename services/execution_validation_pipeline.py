from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from services.execution_models import CopyExecutionPlan, ExecutionMode


@dataclass(frozen=True)
class ValidationFailure:
    validator: str
    code: str
    reason: str


@dataclass(frozen=True)
class ValidationResult:
    allowed: bool
    code: str
    reason: str
    failures: tuple[ValidationFailure, ...] = ()


class PlanValidator(Protocol):
    name: str
    def validate(self, plan: CopyExecutionPlan, *, mode: ExecutionMode,
                 adapter_mode: ExecutionMode) -> tuple[ValidationFailure, ...]: ...


class PlanIdentityValidator:
    name = "plan_identity"

    def validate(self, plan, **_):
        failures = []
        if not str(plan.symbol or "").strip():
            failures.append(ValidationFailure(self.name, "MISSING_SYMBOL", "Symbol is required"))
        if plan.telegram_id <= 0:
            failures.append(ValidationFailure(self.name, "INVALID_OWNER", "Owner is required"))
        if plan.signal_id <= 0 or not plan.idempotency_key or not plan.plan_id:
            failures.append(ValidationFailure(self.name, "INVALID_IDENTITY", "Stable plan identity is required"))
        return tuple(failures)


class OrderPayloadValidator:
    name = "order_payload"

    def validate(self, plan, **_):
        failures = []
        if not str(plan.symbol or "").strip():
            failures.append(ValidationFailure(self.name, "MISSING_SYMBOL", "Order symbol is required"))
        if plan.quantity is None or plan.quantity <= 0:
            failures.append(ValidationFailure(self.name, "INVALID_QUANTITY", "Quantity must be positive"))
        if plan.entry_price is None or plan.entry_price <= 0:
            failures.append(ValidationFailure(self.name, "INVALID_ENTRY", "Entry price must be positive"))
        if plan.stop_loss is None or plan.stop_loss <= 0:
            failures.append(ValidationFailure(self.name, "MISSING_STOP", "A positive stop is required"))
        if str(plan.side or "").upper() not in {"LONG", "SHORT"}:
            failures.append(ValidationFailure(self.name, "INVALID_SIDE", "Side must be LONG or SHORT"))
        return tuple(failures)


class PaperSafetyValidator:
    name = "paper_safety"

    def validate(self, plan, *, mode, adapter_mode, **_):
        if mode is not ExecutionMode.PAPER or adapter_mode is not ExecutionMode.PAPER:
            return (ValidationFailure(self.name, "LIVE_DISABLED", "LIVE execution is disabled"),)
        return ()


class ExecutionValidationPipeline:
    def __init__(self, validators: tuple[PlanValidator, ...] | None = None) -> None:
        self.validators = validators or (
            PlanIdentityValidator(), OrderPayloadValidator(), PaperSafetyValidator(),
        )

    def validate(self, plan: CopyExecutionPlan, *, mode: ExecutionMode,
                 adapter_mode: ExecutionMode) -> ValidationResult:
        failures = tuple(
            failure for validator in self.validators
            for failure in validator.validate(plan, mode=mode, adapter_mode=adapter_mode)
        )
        if failures:
            first = failures[0]
            return ValidationResult(False, first.code, first.reason, failures)
        return ValidationResult(True, "VALIDATED", "Execution contract validated")
