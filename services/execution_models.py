from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ExecutionMode(str, Enum):
    PAPER = "PAPER"
    LIVE = "LIVE"


class ExecutionStatus(str, Enum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    PARTIAL = "PARTIAL"
    CLOSED = "CLOSED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class RiskProfile:
    risk_pct: float = 0.5
    max_positions: int = 3
    max_heat_r: float = 2.5
    daily_loss_pct: float = 2.0
    max_slippage_pct: float = 0.25
    paper_balance: float = 10_000.0


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
