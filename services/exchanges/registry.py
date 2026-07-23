from __future__ import annotations

import os
from collections.abc import Callable

from services.exchanges.base import ExchangeAdapter
from services.exchanges.binance_usdm import BinanceUsdmAdapter
from services.exchanges.bybit_v5 import BybitV5Adapter
from services.exchanges.bingx_swap import BingXSwapAdapter
from services.exchanges.models import ExchangeCredentials, ExchangeName
from services.exchanges.okx_v5 import OkxV5Adapter


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
    registry.register(
        ExchangeName.BYBIT,
        lambda: BybitV5Adapter(
            ExchangeCredentials(
                api_key=os.getenv("BYBIT_API_KEY", "").strip(),
                api_secret=os.getenv("BYBIT_API_SECRET", "").strip(),
                testnet=os.getenv("BYBIT_TESTNET", "true").strip().lower() in {"1", "true", "yes", "on"},
            ),
            recv_window=int(os.getenv("BYBIT_RECV_WINDOW", "5000")),
            timeout_seconds=float(os.getenv("EXCHANGE_HTTP_TIMEOUT", "10")),
        ),
    )
    registry.register(
        ExchangeName.BINGX,
        lambda: BingXSwapAdapter(
            ExchangeCredentials(
                api_key=os.getenv("BINGX_API_KEY", "").strip(),
                api_secret=os.getenv("BINGX_API_SECRET", "").strip(),
                testnet=os.getenv("BINGX_DEMO", "true").strip().lower() in {"1", "true", "yes", "on"},
            ),
            recv_window=int(os.getenv("BINGX_RECV_WINDOW", "5000")),
            timeout_seconds=float(os.getenv("EXCHANGE_HTTP_TIMEOUT", "10")),
            connect_timeout_seconds=float(os.getenv("EXCHANGE_CONNECT_TIMEOUT", "5")),
            read_timeout_seconds=float(os.getenv("EXCHANGE_READ_TIMEOUT", "12")),
            max_attempts=int(os.getenv("EXCHANGE_MAX_ATTEMPTS", "3")),
            retry_backoff_seconds=float(os.getenv("EXCHANGE_RETRY_BACKOFF", "0.35")),
            symbol_cache_ttl_seconds=float(os.getenv("EXCHANGE_SYMBOL_CACHE_TTL", "300")),
        ),
    )
    registry.register(
        ExchangeName.OKX,
        lambda: OkxV5Adapter(
            ExchangeCredentials(
                api_key=os.getenv("OKX_API_KEY", "").strip(),
                api_secret=os.getenv("OKX_API_SECRET", "").strip(),
                testnet=os.getenv("OKX_DEMO", "true").strip().lower() in {"1", "true", "yes", "on"},
            ),
            passphrase=os.getenv("OKX_API_PASSPHRASE", "").strip(),
            timeout_seconds=float(os.getenv("EXCHANGE_HTTP_TIMEOUT", "10")),
            connect_timeout_seconds=float(os.getenv("EXCHANGE_CONNECT_TIMEOUT", "5")),
            read_timeout_seconds=float(os.getenv("EXCHANGE_READ_TIMEOUT", "12")),
            max_attempts=int(os.getenv("EXCHANGE_MAX_ATTEMPTS", "3")),
            retry_backoff_seconds=float(os.getenv("EXCHANGE_RETRY_BACKOFF", "0.35")),
            symbol_cache_ttl_seconds=float(os.getenv("EXCHANGE_SYMBOL_CACHE_TTL", "300")),
        ),
    )
    return registry
