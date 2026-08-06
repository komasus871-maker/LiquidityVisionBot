from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class ExchangeStatus(StrEnum):
    CONNECTED = "connected"
    PUBLIC_ONLY = "public_only"
    NOT_CONFIGURED = "not_configured"
    GEO_BLOCKED = "geo_blocked"
    AUTH_FAILED = "auth_failed"
    UNAVAILABLE = "unavailable"


class ExchangeName(StrEnum):
    BINANCE = "binance"
    BYBIT = "bybit"
    BINGX = "bingx"
    BITUNIX = "bitunix"
    OKX = "okx"


class ExchangeCapability(StrEnum):
    ACCOUNT_SYNC = "account_sync"
    BALANCES = "balances"
    SYMBOL_RULES = "symbol_rules"
    LEVERAGE = "leverage"
    MARGIN_MODE = "margin_mode"
    PLACE_ORDER = "place_order"
    CANCEL_ORDER = "cancel_order"
    QUERY_ORDER = "query_order"
    QUERY_BY_CLIENT_ID = "query_by_client_id"
    OPEN_ORDERS = "open_orders"
    FILLS = "fills"
    POSITIONS = "positions"
    REDUCE_ONLY = "reduce_only"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    SERVER_TIME = "server_time"
    RATE_LIMITS = "rate_limits"


@dataclass(frozen=True, slots=True)
class ExchangeCapabilities:
    supported: frozenset[ExchangeCapability] = frozenset()

    def supports(self, capability: ExchangeCapability) -> bool:
        return capability in self.supported


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
    status: ExchangeStatus = ExchangeStatus.UNAVAILABLE
    endpoint: str | None = None


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
    margin_mode: str | None = None
    position_id: str | None = None


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
    client_order_id: str | None = None
    average_price: Decimal | None = None
    commission: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class ExchangeOrderRequest:
    symbol: str
    side: str
    order_type: str
    quantity: Decimal
    client_order_id: str
    price: Decimal | None = None
    leverage: int = 1
    margin_mode: str | None = None
    reduce_only: bool = False
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    position_side: str | None = None
    working_type: str = "MARK_PRICE"


@dataclass(frozen=True, slots=True)
class ExchangeFill:
    fill_id: str
    order_id: str
    client_order_id: str | None
    symbol: str
    side: str
    quantity: Decimal
    price: Decimal
    commission: Decimal = Decimal("0")
    commission_asset: str | None = None
    filled_at_ms: int | None = None
    realized_pnl: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class ExchangeAccountInfo:
    trading_enabled: bool
    withdrawal_enabled: bool | None = None
    margin_mode: str | None = None
    position_mode: str | None = None
    environment: str | None = None


@dataclass(frozen=True, slots=True)
class ExchangeRateLimits:
    requests_remaining: int | None = None
    reset_at_ms: int | None = None


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
    max_quantity: Decimal | None = None
    max_leverage: int | None = None
