from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ExecutionMode(str, Enum):
    PAPER = "PAPER"
    LIVE = "LIVE"


class PositionSizingMode(str, Enum):
    RISK_PERCENT = "RISK_PERCENT"
    FIXED_USDT = "FIXED_USDT"


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
