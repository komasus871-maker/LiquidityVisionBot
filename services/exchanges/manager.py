from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from services.exchanges.base import (
    ExchangeAuthenticationError,
    ExchangeConfigurationError,
    ExchangeRequestError,
)
from services.exchanges.models import (
    ExchangeBalance,
    ExchangeHealth,
    ExchangeName,
    ExchangeOrder,
    ExchangePosition,
    ExchangeStatus,
)
from services.exchanges.registry import ExchangeRegistry


@dataclass(frozen=True, slots=True)
class ExchangeAccountSnapshot:
    exchange: ExchangeName
    health: ExchangeHealth
    balances: tuple[ExchangeBalance, ...]
    positions: tuple[ExchangePosition, ...]
    open_orders: tuple[ExchangeOrder, ...]
    captured_at_ms: int

    @property
    def non_zero_assets(self) -> int:
        return len(self.balances)

    @property
    def open_position_count(self) -> int:
        return len(self.positions)

    @property
    def open_order_count(self) -> int:
        return len(self.open_orders)


class ExchangeManager:
    """Coordinates adapters while keeping credentials ephemeral and read-only."""

    def __init__(self, registry: ExchangeRegistry, *, operation_timeout_seconds: float = 25.0) -> None:
        self.registry = registry
        self.operation_timeout_seconds = max(1.0, float(operation_timeout_seconds))

    async def health(self, exchange: ExchangeName | str) -> ExchangeHealth:
        adapter = self.registry.create(exchange)
        try:
            return await asyncio.wait_for(adapter.health(), timeout=self.operation_timeout_seconds)
        finally:
            await adapter.close()

    async def snapshot(
        self,
        exchange: ExchangeName | str,
        *,
        symbol: str | None = None,
    ) -> ExchangeAccountSnapshot:
        adapter = self.registry.create(exchange)
        try:
            health = await asyncio.wait_for(adapter.health(), timeout=self.operation_timeout_seconds)
            self._require_authenticated(health)
            balances, positions, orders = await asyncio.wait_for(
                asyncio.gather(
                    adapter.balances(),
                    adapter.positions(),
                    adapter.open_orders(symbol),
                ),
                timeout=self.operation_timeout_seconds,
            )
            return ExchangeAccountSnapshot(
                exchange=health.exchange,
                health=health,
                balances=tuple(balances),
                positions=tuple(positions),
                open_orders=tuple(orders),
                captured_at_ms=int(time.time() * 1000),
            )
        except TimeoutError as exc:
            raise ExchangeRequestError(
                f"{str(exchange).upper()} account snapshot exceeded "
                f"{self.operation_timeout_seconds:g}s"
            ) from exc
        finally:
            await adapter.close()

    @staticmethod
    def _require_authenticated(health: ExchangeHealth) -> None:
        if health.authenticated and health.status is ExchangeStatus.CONNECTED:
            return
        if health.status is ExchangeStatus.AUTH_FAILED:
            raise ExchangeAuthenticationError(health.error or "exchange authentication failed")
        if health.status in {ExchangeStatus.PUBLIC_ONLY, ExchangeStatus.NOT_CONFIGURED}:
            raise ExchangeConfigurationError(
                health.error or f"{health.exchange.value.upper()} credentials are not configured"
            )
        raise ExchangeRequestError(health.error or f"{health.exchange.value.upper()} is unavailable")
