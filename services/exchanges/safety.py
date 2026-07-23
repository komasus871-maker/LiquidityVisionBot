from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from services.exchanges.models import ExchangeName, ExchangeOrder, ExchangePosition, SymbolRules


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True, slots=True)
class OrderIntent:
    exchange: ExchangeName
    symbol: str
    side: OrderSide
    quantity: Decimal
    reference_price: Decimal
    leverage: int
    reduce_only: bool = False
    demo: bool = True

    @property
    def notional(self) -> Decimal:
        return abs(self.quantity * self.reference_price)


@dataclass(frozen=True, slots=True)
class ExecutionSafetyPolicy:
    live_enabled: bool = False
    require_demo: bool = True
    max_notional_usdt: Decimal = Decimal("100")
    max_leverage: int = 5
    max_open_positions: int = 3
    allowed_symbols: frozenset[str] = frozenset({"BTCUSDT", "ETHUSDT"})

    @classmethod
    def from_env(cls) -> "ExecutionSafetyPolicy":
        raw_symbols = os.getenv("EXECUTION_ALLOWED_SYMBOLS", "BTCUSDT,ETHUSDT")
        symbols = frozenset(_compact_symbol(value) for value in raw_symbols.split(",") if value.strip())
        return cls(
            live_enabled=_env_bool("LIVE_EXECUTION_ENABLED", False),
            require_demo=_env_bool("EXECUTION_REQUIRE_DEMO", True),
            max_notional_usdt=_env_decimal("EXECUTION_MAX_NOTIONAL_USDT", "100"),
            max_leverage=max(1, _env_int("EXECUTION_MAX_LEVERAGE", 5)),
            max_open_positions=max(0, _env_int("EXECUTION_MAX_OPEN_POSITIONS", 3)),
            allowed_symbols=symbols,
        )


@dataclass(frozen=True, slots=True)
class SafetyDecision:
    approved: bool
    violations: tuple[str, ...]
    warnings: tuple[str, ...]
    normalized_symbol: str
    notional: Decimal


class ExecutionSafetyValidator:
    """Pure preflight validator. It cannot submit, modify, or cancel orders."""

    def __init__(self, policy: ExecutionSafetyPolicy) -> None:
        self.policy = policy

    def validate(
        self,
        intent: OrderIntent,
        rules: SymbolRules,
        *,
        positions: tuple[ExchangePosition, ...] = (),
        open_orders: tuple[ExchangeOrder, ...] = (),
    ) -> SafetyDecision:
        violations: list[str] = []
        warnings: list[str] = []
        normalized = _compact_symbol(rules.symbol)

        if intent.quantity <= 0:
            violations.append("quantity_must_be_positive")
        if intent.reference_price <= 0:
            violations.append("reference_price_must_be_positive")
        if intent.leverage < 1:
            violations.append("leverage_must_be_positive")
        if intent.leverage > self.policy.max_leverage:
            violations.append(f"leverage_exceeds_limit:{self.policy.max_leverage}")
        if self.policy.allowed_symbols and normalized not in self.policy.allowed_symbols:
            violations.append("symbol_not_whitelisted")
        if intent.notional > self.policy.max_notional_usdt:
            violations.append(f"notional_exceeds_limit:{self.policy.max_notional_usdt}")
        if intent.quantity < rules.min_quantity:
            violations.append(f"quantity_below_minimum:{rules.min_quantity}")
        if rules.min_notional is not None and intent.notional < rules.min_notional:
            violations.append(f"notional_below_minimum:{rules.min_notional}")
        if rules.quantity_step > 0 and not _aligned(intent.quantity, rules.quantity_step):
            violations.append(f"quantity_not_aligned:{rules.quantity_step}")
        if rules.price_tick > 0 and not _aligned(intent.reference_price, rules.price_tick):
            violations.append(f"price_not_aligned:{rules.price_tick}")
        if self.policy.require_demo and not intent.demo:
            violations.append("demo_environment_required")
        if not intent.demo and not self.policy.live_enabled:
            violations.append("live_execution_locked")

        same_symbol_positions = [p for p in positions if _compact_symbol(p.symbol) == normalized]
        if not intent.reduce_only and len(positions) >= self.policy.max_open_positions and not same_symbol_positions:
            violations.append(f"max_open_positions_reached:{self.policy.max_open_positions}")
        duplicate = any(
            _compact_symbol(order.symbol) == normalized
            and order.side.upper() == intent.side.value
            and order.status.upper() not in {"CANCELED", "CANCELLED", "FILLED", "REJECTED"}
            for order in open_orders
        )
        if duplicate and not intent.reduce_only:
            violations.append("duplicate_open_order")
        if not positions and not open_orders:
            warnings.append("portfolio_state_not_supplied")
        if rules.min_notional is None:
            warnings.append("exchange_did_not_publish_min_notional")

        return SafetyDecision(
            approved=not violations,
            violations=tuple(violations),
            warnings=tuple(warnings),
            normalized_symbol=rules.symbol,
            notional=intent.notional,
        )


def _aligned(value: Decimal, step: Decimal) -> bool:
    if step <= 0:
        return True
    return value % step == 0


def _compact_symbol(symbol: str) -> str:
    raw = symbol.strip().upper().replace("-", "").replace("_", "")
    return raw[:-4] if raw.endswith("SWAP") else raw


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_decimal(name: str, default: str) -> Decimal:
    try:
        return Decimal(os.getenv(name, default))
    except (InvalidOperation, ValueError):
        return Decimal(default)
