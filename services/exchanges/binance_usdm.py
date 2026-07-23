from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from decimal import Decimal
from typing import Any, Mapping
from urllib.parse import urlencode

import aiohttp

from services.exchanges.base import (
    ExchangeAdapter,
    ExchangeAuthenticationError,
    ExchangeConfigurationError,
    ExchangeRequestError,
)
from services.exchanges.models import (
    ExchangeBalance,
    ExchangeCredentials,
    ExchangeHealth,
    ExchangeName,
    ExchangeOrder,
    ExchangePosition,
    SymbolRules,
)


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value or "0"))


class BinanceUsdmAdapter(ExchangeAdapter):
    """Read-only Binance USD-M Futures REST adapter.

    No method in this release can create, modify, or cancel an order.
    """

    PRODUCTION_URL = "https://fapi.binance.com"
    TESTNET_URL = "https://demo-fapi.binance.com"

    def __init__(
        self,
        credentials: ExchangeCredentials,
        *,
        recv_window: int = 5_000,
        timeout_seconds: float = 10.0,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self.credentials = credentials
        self.recv_window = max(1_000, min(int(recv_window), 60_000))
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.base_url = self.TESTNET_URL if credentials.testnet else self.PRODUCTION_URL
        self._session = session
        self._owns_session = session is None
        self._time_offset_ms = 0

    async def _client(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
            self._session = aiohttp.ClientSession(timeout=timeout)
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        if self._owns_session and self._session is not None and not self._session.closed:
            await self._session.close()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        signed: bool = False,
    ) -> Any:
        payload = {key: value for key, value in (params or {}).items() if value is not None}
        headers: dict[str, str] = {}
        if signed:
            if not self.credentials.configured:
                raise ExchangeConfigurationError("BINANCE_API_KEY and BINANCE_API_SECRET are required")
            payload["timestamp"] = int(time.time() * 1000) + self._time_offset_ms
            payload["recvWindow"] = self.recv_window
            query = urlencode(payload, doseq=True)
            payload["signature"] = hmac.new(
                self.credentials.api_secret.encode("utf-8"),
                query.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            headers["X-MBX-APIKEY"] = self.credentials.api_key

        session = await self._client()
        try:
            async with session.request(method, f"{self.base_url}{path}", params=payload, headers=headers) as response:
                data = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            raise ExchangeRequestError(f"Binance request failed: {type(exc).__name__}") from exc

        if response.status >= 400:
            code = data.get("code") if isinstance(data, dict) else None
            message = data.get("msg") if isinstance(data, dict) else str(data)
            safe_message = str(message or "remote error")[:240]
            if response.status in {401, 403} or code in {-2014, -2015, -1022}:
                raise ExchangeAuthenticationError(f"Binance authentication failed ({code}): {safe_message}")
            raise ExchangeRequestError(f"Binance API error {response.status} ({code}): {safe_message}")
        return data

    async def _server_time(self) -> tuple[int, float]:
        started = time.perf_counter()
        data = await self._request("GET", "/fapi/v1/time")
        latency_ms = (time.perf_counter() - started) * 1000
        server_time = int(data["serverTime"])
        self._time_offset_ms = server_time - int(time.time() * 1000)
        return server_time, latency_ms

    async def health(self) -> ExchangeHealth:
        try:
            server_time, latency_ms = await self._server_time()
            if not self.credentials.configured:
                return ExchangeHealth(
                    exchange=ExchangeName.BINANCE,
                    reachable=True,
                    authenticated=False,
                    testnet=self.credentials.testnet,
                    latency_ms=round(latency_ms, 2),
                    server_time_ms=server_time,
                    error="credentials_not_configured",
                )
            await self._request("GET", "/fapi/v3/balance", signed=True)
            return ExchangeHealth(
                exchange=ExchangeName.BINANCE,
                reachable=True,
                authenticated=True,
                testnet=self.credentials.testnet,
                latency_ms=round(latency_ms, 2),
                server_time_ms=server_time,
            )
        except (ExchangeConfigurationError, ExchangeAuthenticationError, ExchangeRequestError) as exc:
            return ExchangeHealth(
                exchange=ExchangeName.BINANCE,
                reachable=not isinstance(exc, ExchangeRequestError) or "request failed" not in str(exc).lower(),
                authenticated=False,
                testnet=self.credentials.testnet,
                error=str(exc),
            )

    async def balances(self) -> list[ExchangeBalance]:
        data = await self._request("GET", "/fapi/v3/balance", signed=True)
        return [
            ExchangeBalance(
                asset=str(item.get("asset") or ""),
                wallet_balance=_decimal(item.get("balance")),
                available_balance=_decimal(item.get("availableBalance")),
                unrealized_pnl=_decimal(item.get("crossUnPnl")),
            )
            for item in data
            if _decimal(item.get("balance")) != 0 or _decimal(item.get("availableBalance")) != 0
        ]

    async def positions(self) -> list[ExchangePosition]:
        data = await self._request("GET", "/fapi/v3/positionRisk", signed=True)
        positions: list[ExchangePosition] = []
        for item in data:
            quantity = _decimal(item.get("positionAmt"))
            if quantity == 0:
                continue
            side = "LONG" if quantity > 0 else "SHORT"
            liquidation = _decimal(item.get("liquidationPrice"))
            positions.append(
                ExchangePosition(
                    symbol=str(item.get("symbol") or ""),
                    side=side,
                    quantity=abs(quantity),
                    entry_price=_decimal(item.get("entryPrice")),
                    mark_price=_decimal(item.get("markPrice")),
                    unrealized_pnl=_decimal(item.get("unRealizedProfit")),
                    leverage=int(item.get("leverage") or 0),
                    liquidation_price=liquidation if liquidation > 0 else None,
                )
            )
        return positions

    async def open_orders(self, symbol: str | None = None) -> list[ExchangeOrder]:
        params = {"symbol": symbol.upper()} if symbol else None
        data = await self._request("GET", "/fapi/v1/openOrders", params=params, signed=True)
        return [
            ExchangeOrder(
                order_id=str(item.get("orderId") or ""),
                symbol=str(item.get("symbol") or ""),
                side=str(item.get("side") or ""),
                order_type=str(item.get("type") or ""),
                status=str(item.get("status") or ""),
                quantity=_decimal(item.get("origQty")),
                executed_quantity=_decimal(item.get("executedQty")),
                price=_decimal(item.get("price")) if _decimal(item.get("price")) > 0 else None,
                stop_price=_decimal(item.get("stopPrice")) if _decimal(item.get("stopPrice")) > 0 else None,
                reduce_only=bool(item.get("reduceOnly")),
            )
            for item in data
        ]

    async def symbol_rules(self, symbol: str) -> SymbolRules:
        normalized = symbol.upper()
        data = await self._request("GET", "/fapi/v1/exchangeInfo")
        item = next((row for row in data.get("symbols", []) if row.get("symbol") == normalized), None)
        if item is None:
            raise ExchangeRequestError(f"Binance symbol {normalized} was not found")
        filters = {entry.get("filterType"): entry for entry in item.get("filters", [])}
        price_filter = filters.get("PRICE_FILTER", {})
        lot_filter = filters.get("LOT_SIZE", {})
        notional_filter = filters.get("MIN_NOTIONAL", {})
        min_notional = _decimal(notional_filter.get("notional"))
        return SymbolRules(
            symbol=normalized,
            status=str(item.get("status") or ""),
            base_asset=str(item.get("baseAsset") or ""),
            quote_asset=str(item.get("quoteAsset") or ""),
            price_tick=_decimal(price_filter.get("tickSize")),
            quantity_step=_decimal(lot_filter.get("stepSize")),
            min_quantity=_decimal(lot_filter.get("minQty")),
            min_notional=min_notional if min_notional > 0 else None,
            raw=dict(item),
        )
