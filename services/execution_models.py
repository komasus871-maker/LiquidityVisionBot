from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ExecutionMode(str, Enum):
    PAPER = "PAPER"
    LIVE = "LIVE"


class PositionSizingMode(str, Enum):
    RISK_PERCENT = "RISK_PERCENT"
    FIXED_USDT = "FIXED_USDT"


class ExecutionPlanStatus(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ExecutionStatus(str, Enum):
    PLANNED = "PLANNED"
    VALIDATED = "VALIDATED"
    QUEUED = "QUEUED"
    PENDING = "PENDING"
    OPEN = "OPEN"
    PARTIAL = "PARTIAL"
    CLOSED = "CLOSED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class RiskProfile:
    risk_pct: float = 0.5
    sizing_mode: PositionSizingMode = PositionSizingMode.RISK_PERCENT
    fixed_usdt: float = 0.0
    leverage: int = 1
    auto_copy: bool = False
    max_positions: int = 3
    max_heat_r: float = 2.5
    daily_loss_pct: float = 2.0
    max_slippage_pct: float = 0.25
    paper_balance: float = 10_000.0
    min_confidence: float = 55.0
    max_notional_pct: float = 35.0
    symbol_cooldown_min: int = 30


@dataclass(frozen=True)
class PortfolioState:
    open_positions: int = 0
    current_heat_r: float = 0.0
    daily_realized_pnl: float = 0.0
    symbol_is_open: bool = False
    symbol_in_cooldown: bool = False


@dataclass(frozen=True)
class PositionSize:
    quantity: float
    notional: float
    risk_amount: float
    stop_distance_pct: float


@dataclass(frozen=True)
class ExecutionDecision:
    allowed: bool
    code: str
    reason: str
    size: PositionSize | None = None
    expected_slippage_pct: float = 0.0
    risk_multiplier: float = 1.0
    training_sample_size: int = 0


@dataclass(frozen=True)
class CopyExecutionPlan:
    plan_id: str
    idempotency_key: str
    status: ExecutionPlanStatus
    code: str
    reason: str
    telegram_id: int
    signal_id: int
    exchange_account_id: int | None
    symbol: str
    timeframe: str
    side: str
    order_type: str
    entry_price: float | None
    quantity: float | None = None
    notional: float | None = None
    leverage: int = 1
    stop_loss: float | None = None
    take_profits: tuple[float, ...] = ()
    risk_amount: float | None = None
    stop_distance_pct: float | None = None
    sizing_mode: str = PositionSizingMode.RISK_PERCENT.value
    expected_slippage_pct: float = 0.0
    risk_multiplier: float = 1.0
    training_sample_size: int = 0
    profile_snapshot: dict[str, object] | None = None

    @property
    def approved(self) -> bool:
        return self.status is ExecutionPlanStatus.APPROVED


ALLOWED_EXECUTION_TRANSITIONS = {
    ExecutionStatus.PLANNED: {ExecutionStatus.VALIDATED, ExecutionStatus.CANCELLED},
    ExecutionStatus.VALIDATED: {ExecutionStatus.QUEUED, ExecutionStatus.REJECTED},
    ExecutionStatus.QUEUED: {ExecutionStatus.PENDING, ExecutionStatus.CANCELLED},
    ExecutionStatus.PENDING: {ExecutionStatus.OPEN, ExecutionStatus.REJECTED},
    ExecutionStatus.OPEN: {ExecutionStatus.PARTIAL, ExecutionStatus.CLOSED, ExecutionStatus.CANCELLED},
    ExecutionStatus.PARTIAL: {ExecutionStatus.CLOSED, ExecutionStatus.CANCELLED},
}

def can_transition_execution_state(current: ExecutionStatus, target: ExecutionStatus)->bool:
    if current==target:
        return True
    return target in ALLOWED_EXECUTION_TRANSITIONS.get(current,set())
