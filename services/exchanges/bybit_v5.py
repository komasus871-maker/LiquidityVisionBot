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
    ExchangeStatus,
    SymbolRules,
)


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value or "0"))


class BybitV5Adapter(ExchangeAdapter):
    """Read-only Bybit V5 linear-contract adapter."""

    PRODUCTION_URL = "https://api.bybit.com"
    TESTNET_URL = "https://api-testnet.bybit.com"

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
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout_seconds))
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        if self._owns_session and self._session is not None and not self._session.closed:
            await self._session.close()

    async def _request(self, path: str, *, params: Mapping[str, Any] | None = None, signed: bool = False) -> Any:
        payload = {key: value for key, value in (params or {}).items() if value is not None}
        query = urlencode(sorted(payload.items()), doseq=True)
        headers: dict[str, str] = {}
        if signed:
            if not self.credentials.configured:
                raise ExchangeConfigurationError("BYBIT_API_KEY and BYBIT_API_SECRET are required")
            timestamp = str(int(time.time() * 1000) + self._time_offset_ms)
            sign_payload = f"{timestamp}{self.credentials.api_key}{self.recv_window}{query}"
            signature = hmac.new(
                self.credentials.api_secret.encode(), sign_payload.encode(), hashlib.sha256
            ).hexdigest()
            headers = {
                "X-BAPI-API-KEY": self.credentials.api_key,
                "X-BAPI-SIGN": signature,
                "X-BAPI-TIMESTAMP": timestamp,
                "X-BAPI-RECV-WINDOW": str(self.recv_window),
                "X-BAPI-SIGN-TYPE": "2",
            }

        session = await self._client()
        try:
            async with session.get(f"{self.base_url}{path}", params=payload, headers=headers) as response:
                data = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            raise ExchangeRequestError(f"Bybit request failed: {type(exc).__name__}") from exc

        ret_code = data.get("retCode") if isinstance(data, dict) else None
        ret_msg = data.get("retMsg") if isinstance(data, dict) else str(data)
        if response.status >= 400 or ret_code not in {None, 0}:
            safe = str(ret_msg or "remote error")[:240]
            if response.status in {401, 403} or ret_code in {10003, 10004, 10005, 10007, 33004}:
                raise ExchangeAuthenticationError(f"Bybit authentication failed ({ret_code}): {safe}")
            raise ExchangeRequestError(f"Bybit API error {response.status} ({ret_code}): {safe}")
        return data.get("result", data)

    async def _server_time(self) -> tuple[int, float]:
        started = time.perf_counter()
        result = await self._request("/v5/market/time")
        latency_ms = (time.perf_counter() - started) * 1000
        server_time_ms = int(result["timeSecond"]) * 1000
        self._time_offset_ms = server_time_ms - int(time.time() * 1000)
        return server_time_ms, latency_ms

    async def health(self) -> ExchangeHealth:
        try:
            server_time, latency_ms = await self._server_time()
            if not self.credentials.configured:
                return ExchangeHealth(
                    exchange=ExchangeName.BYBIT, reachable=True, authenticated=False,
                    testnet=self.credentials.testnet, latency_ms=round(latency_ms, 2),
                    server_time_ms=server_time, status=ExchangeStatus.PUBLIC_ONLY,
                    error="credentials_not_configured", endpoint=self.base_url,
                )
            await self._request("/v5/account/wallet-balance", params={"accountType": "UNIFIED"}, signed=True)
            return ExchangeHealth(
                exchange=ExchangeName.BYBIT, reachable=True, authenticated=True,
                testnet=self.credentials.testnet, latency_ms=round(latency_ms, 2),
                server_time_ms=server_time, status=ExchangeStatus.CONNECTED, endpoint=self.base_url,
            )
        except ExchangeConfigurationError as exc:
            return ExchangeHealth(ExchangeName.BYBIT, True, False, self.credentials.testnet,
                                  status=ExchangeStatus.NOT_CONFIGURED, error=str(exc), endpoint=self.base_url)
        except ExchangeAuthenticationError as exc:
            return ExchangeHealth(ExchangeName.BYBIT, True, False, self.credentials.testnet,
                                  status=ExchangeStatus.AUTH_FAILED, error=str(exc), endpoint=self.base_url)
        except ExchangeRequestError as exc:
            return ExchangeHealth(ExchangeName.BYBIT, False, False, self.credentials.testnet,
                                  status=ExchangeStatus.UNAVAILABLE, error=str(exc), endpoint=self.base_url)

    async def balances(self) -> list[ExchangeBalance]:
        result = await self._request("/v5/account/wallet-balance", params={"accountType": "UNIFIED"}, signed=True)
        accounts = result.get("list", [])
        coins = accounts[0].get("coin", []) if accounts else []
        return [
            ExchangeBalance(
                asset=str(item.get("coin") or ""),
                wallet_balance=_decimal(item.get("walletBalance")),
                available_balance=_decimal(item.get("availableToWithdraw") or item.get("availableToBorrow")),
                unrealized_pnl=_decimal(item.get("unrealisedPnl")),
            )
            for item in coins
            if _decimal(item.get("walletBalance")) != 0 or _decimal(item.get("equity")) != 0
        ]

    async def positions(self) -> list[ExchangePosition]:
        result = await self._request("/v5/position/list", params={"category": "linear", "settleCoin": "USDT"}, signed=True)
        positions: list[ExchangePosition] = []
        for item in result.get("list", []):
            quantity = _decimal(item.get("size"))
            if quantity == 0:
                continue
            liquidation = _decimal(item.get("liqPrice"))
            positions.append(ExchangePosition(
                symbol=str(item.get("symbol") or ""), side=str(item.get("side") or "").upper(),
                quantity=quantity, entry_price=_decimal(item.get("avgPrice")),
                mark_price=_decimal(item.get("markPrice")), unrealized_pnl=_decimal(item.get("unrealisedPnl")),
                leverage=int(_decimal(item.get("leverage"))), liquidation_price=liquidation if liquidation > 0 else None,
            ))
        return positions

    async def open_orders(self, symbol: str | None = None) -> list[ExchangeOrder]:
        params = {"category": "linear", "openOnly": 0, "limit": 50, "symbol": symbol.upper() if symbol else None}
        result = await self._request("/v5/order/realtime", params=params, signed=True)
        return [ExchangeOrder(
            order_id=str(item.get("orderId") or ""), symbol=str(item.get("symbol") or ""),
            side=str(item.get("side") or "").upper(), order_type=str(item.get("orderType") or ""),
            status=str(item.get("orderStatus") or ""), quantity=_decimal(item.get("qty")),
            executed_quantity=_decimal(item.get("cumExecQty")),
            price=_decimal(item.get("price")) if _decimal(item.get("price")) > 0 else None,
            stop_price=_decimal(item.get("triggerPrice")) if _decimal(item.get("triggerPrice")) > 0 else None,
            reduce_only=bool(item.get("reduceOnly")),
        ) for item in result.get("list", [])]

    async def symbol_rules(self, symbol: str) -> SymbolRules:
        normalized = symbol.upper()
        result = await self._request("/v5/market/instruments-info", params={"category": "linear", "symbol": normalized})
        items = result.get("list", [])
        if not items:
            raise ExchangeRequestError(f"Bybit symbol {normalized} was not found")
        item = items[0]
        price_filter = item.get("priceFilter", {})
        lot_filter = item.get("lotSizeFilter", {})
        return SymbolRules(
            symbol=normalized, status=str(item.get("status") or ""),
            base_asset=str(item.get("baseCoin") or ""), quote_asset=str(item.get("quoteCoin") or ""),
            price_tick=_decimal(price_filter.get("tickSize")), quantity_step=_decimal(lot_filter.get("qtyStep")),
            min_quantity=_decimal(lot_filter.get("minOrderQty")),
            min_notional=_decimal(lot_filter.get("minNotionalValue")) or None, raw=dict(item),
        )
