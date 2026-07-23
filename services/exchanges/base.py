from __future__ import annotations

from abc import ABC, abstractmethod

from decimal import Decimal

from services.exchanges.models import (
    ExchangeBalance,
    ExchangeHealth,
    ExchangeOrder,
    ExchangePosition,
    SymbolRules,
)


class ExchangeError(RuntimeError):
    """Base exception for normalized exchange failures."""


class ExchangeConfigurationError(ExchangeError):
    """Raised when authenticated operations are requested without credentials."""


class ExchangeAuthenticationError(ExchangeError):
    """Raised when an exchange rejects credentials or a request signature."""


class ExchangeRequestError(ExchangeError):
    """Raised for transport, rate-limit, validation, or remote API failures."""


class ExchangeTimeoutError(ExchangeRequestError):
    """Raised when an exchange request exceeds its connect/read deadline."""


class ExchangeRateLimitError(ExchangeRequestError):
    """Raised when an exchange asks the client to slow down."""


class ExchangeResponseError(ExchangeRequestError):
    """Raised when a remote response cannot be safely decoded or validated."""


class ExchangeAdapter(ABC):
    """Read-only exchange contract used before LIVE order execution is enabled."""

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
