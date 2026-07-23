from __future__ import annotations

import os
from collections.abc import Callable

from services.exchanges.base import ExchangeAdapter
from services.exchanges.binance_usdm import BinanceUsdmAdapter
from services.exchanges.models import ExchangeCredentials, ExchangeName


AdapterFactory = Callable[[], ExchangeAdapter]


class ExchangeRegistry:
    """Creates configured exchange adapters without persisting secrets."""

    def __init__(self) -> None:
        self._factories: dict[ExchangeName, AdapterFactory] = {}

    def register(self, exchange: ExchangeName, factory: AdapterFactory) -> None:
        self._factories[exchange] = factory

    def create(self, exchange: ExchangeName | str) -> ExchangeAdapter:
        name = exchange if isinstance(exchange, ExchangeName) else ExchangeName(str(exchange).lower())
        try:
            factory = self._factories[name]
        except KeyError as exc:
            raise LookupError(f"Exchange adapter '{name.value}' is not registered") from exc
        return factory()

    def available(self) -> tuple[ExchangeName, ...]:
        return tuple(sorted(self._factories, key=lambda item: item.value))


def build_exchange_registry() -> ExchangeRegistry:
    registry = ExchangeRegistry()
    registry.register(
        ExchangeName.BINANCE,
        lambda: BinanceUsdmAdapter(
            ExchangeCredentials(
                api_key=os.getenv("BINANCE_API_KEY", "").strip(),
                api_secret=os.getenv("BINANCE_API_SECRET", "").strip(),
                testnet=os.getenv("BINANCE_TESTNET", "true").strip().lower() in {"1", "true", "yes", "on"},
            ),
            recv_window=int(os.getenv("BINANCE_RECV_WINDOW", "5000")),
            timeout_seconds=float(os.getenv("EXCHANGE_HTTP_TIMEOUT", "10")),
        ),
    )
    return registry
