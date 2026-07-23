from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any


class ExchangeName(StrEnum):
    BINANCE = "binance"
    BYBIT = "bybit"
    BINGX = "bingx"
    BITUNIX = "bitunix"


@dataclass(frozen=True, slots=True)
class ExchangeCredentials:
    api_key: str
    api_secret: str
    testnet: bool = False

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.api_secret)


@dataclass(frozen=True, slots=True)
class ExchangeHealth:
    exchange: ExchangeName
    reachable: bool
    authenticated: bool
    testnet: bool
    latency_ms: float | None = None
    server_time_ms: int | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ExchangeBalance:
    asset: str
    wallet_balance: Decimal
    available_balance: Decimal
    unrealized_pnl: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class ExchangePosition:
    symbol: str
    side: str
    quantity: Decimal
    entry_price: Decimal
    mark_price: Decimal
    unrealized_pnl: Decimal
    leverage: int
    liquidation_price: Decimal | None = None


@dataclass(frozen=True, slots=True)
class ExchangeOrder:
    order_id: str
    symbol: str
    side: str
    order_type: str
    status: str
    quantity: Decimal
    executed_quantity: Decimal
    price: Decimal | None = None
    stop_price: Decimal | None = None
    reduce_only: bool = False


@dataclass(frozen=True, slots=True)
class SymbolRules:
    symbol: str
    status: str
    base_asset: str
    quote_asset: str
    price_tick: Decimal
    quantity_step: Decimal
    min_quantity: Decimal
    min_notional: Decimal | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
