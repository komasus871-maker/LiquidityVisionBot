from __future__ import annotations

from abc import ABC, abstractmethod

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

    async def close(self) -> None:
        """Release transport resources. Stateless adapters may keep the default no-op."""
