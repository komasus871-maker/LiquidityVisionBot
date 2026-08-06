from __future__ import annotations

from abc import ABC, abstractmethod

from decimal import Decimal

from services.exchanges.models import (
    ExchangeAccountInfo,
    ExchangeBalance,
    ExchangeCapabilities,
    ExchangeCapability,
    ExchangeFill,
    ExchangeHealth,
    ExchangeOrder,
    ExchangePosition,
    ExchangeOrderRequest,
    ExchangeRateLimits,
    SymbolRules,
)


class ExchangeError(RuntimeError):
    """Base exception for normalized exchange failures."""

    code = "EXCHANGE_ERROR"
    retryable = False
    ambiguous_submission = False

    def __init__(self, message: str = "exchange operation failed") -> None:
        import re
        sanitized = re.sub(
            r"(?i)(api[_-]?key|secret|signature|passphrase|token)\s*[:=]\s*[^\s&,;]+",
            r"\1=[REDACTED]", str(message),
        )
        super().__init__(sanitized[:500])


class ExchangeConfigurationError(ExchangeError):
    """Raised when authenticated operations are requested without credentials."""
    code = "CONFIGURATION_ERROR"


class ExchangeAuthenticationError(ExchangeError):
    """Raised when an exchange rejects credentials or a request signature."""
    code = "AUTHENTICATION_FAILED"


class ExchangeRequestError(ExchangeError):
    """Raised for transport, rate-limit, validation, or remote API failures."""
    code = "REQUEST_FAILED"


class ExchangeTimeoutError(ExchangeRequestError):
    """Raised when an exchange request exceeds its connect/read deadline."""
    code = "TIMEOUT"
    retryable = True
    ambiguous_submission = True


class ExchangeRateLimitError(ExchangeRequestError):
    """Raised when an exchange asks the client to slow down."""
    code = "RATE_LIMIT"
    retryable = True


class ExchangeResponseError(ExchangeRequestError):
    """Raised when a remote response cannot be safely decoded or validated."""
    code = "INVALID_RESPONSE"


class ExchangeTimestampError(ExchangeRequestError):
    code = "TIMESTAMP_OUT_OF_SYNC"
    retryable = True


class ExchangeUnsupportedCapabilityError(ExchangeConfigurationError):
    code = "UNSUPPORTED_CAPABILITY"


class ExchangeOrderRejectedError(ExchangeRequestError):
    code = "ORDER_REJECTED"


class ExchangeAdapter(ABC):
    """Backend-neutral contract. Unsupported economic operations fail explicitly."""

    def capabilities(self) -> ExchangeCapabilities:
        return ExchangeCapabilities(frozenset({
            ExchangeCapability.BALANCES,
            ExchangeCapability.SYMBOL_RULES, ExchangeCapability.OPEN_ORDERS,
            ExchangeCapability.POSITIONS,
        }))

    def _unsupported(self, capability: ExchangeCapability):
        raise ExchangeUnsupportedCapabilityError(
            f"{type(self).__name__} does not support {capability.value}"
        )

    @abstractmethod
    async def health(self) -> ExchangeHealth:
        raise NotImplementedError

    @abstractmethod
    async def balances(self) -> list[ExchangeBalance]:
        raise NotImplementedError

    @abstractmethod
    async def positions(self) -> list[ExchangePosition]:
        raise NotImplementedError

    @abstractmethod
    async def open_orders(self, symbol: str | None = None) -> list[ExchangeOrder]:
        raise NotImplementedError

    @abstractmethod
    async def symbol_rules(self, symbol: str) -> SymbolRules:
        raise NotImplementedError

    async def account_info(self) -> ExchangeAccountInfo:
        self._unsupported(ExchangeCapability.ACCOUNT_SYNC)

    async def server_time(self) -> int:
        self._unsupported(ExchangeCapability.SERVER_TIME)

    async def rate_limits(self) -> ExchangeRateLimits:
        self._unsupported(ExchangeCapability.RATE_LIMITS)

    async def set_leverage(self, symbol: str, leverage: int) -> None:
        self._unsupported(ExchangeCapability.LEVERAGE)

    async def set_margin_mode(self, symbol: str, margin_mode: str) -> None:
        self._unsupported(ExchangeCapability.MARGIN_MODE)

    async def place_order(self, request: ExchangeOrderRequest) -> ExchangeOrder:
        self._unsupported(ExchangeCapability.PLACE_ORDER)

    async def cancel_order(self, *, symbol: str, order_id: str) -> ExchangeOrder:
        self._unsupported(ExchangeCapability.CANCEL_ORDER)

    async def query_order(self, *, symbol: str, order_id: str) -> ExchangeOrder | None:
        self._unsupported(ExchangeCapability.QUERY_ORDER)

    async def query_order_by_client_id(self, *, symbol: str, client_order_id: str) -> ExchangeOrder | None:
        self._unsupported(ExchangeCapability.QUERY_BY_CLIENT_ID)

    async def fills(self, *, symbol: str, order_id: str | None = None) -> list[ExchangeFill]:
        self._unsupported(ExchangeCapability.FILLS)


    async def create_demo_order(
        self, *, symbol: str, side: str, order_type: str, quantity: Decimal,
        price: Decimal | None = None, leverage: int = 1, reduce_only: bool = False,
        position_side: str | None = None, client_order_id: str | None = None,
    ) -> ExchangeOrder:
        raise ExchangeConfigurationError(f"{type(self).__name__} does not support demo execution")

    async def cancel_demo_order(self, *, symbol: str, order_id: str) -> ExchangeOrder:
        raise ExchangeConfigurationError(f"{type(self).__name__} does not support demo execution")

    async def demo_order_status(self, *, symbol: str, order_id: str) -> ExchangeOrder:
        raise ExchangeConfigurationError(f"{type(self).__name__} does not support demo execution")

    async def close(self) -> None:
        """Release transport resources. Stateless adapters may keep the default no-op."""
