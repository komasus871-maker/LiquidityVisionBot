from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
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
    ExchangeRateLimitError,
    ExchangeResponseError,
    ExchangeTimeoutError,
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


def _symbol(value: str) -> str:
    raw = value.strip().upper().replace("_", "-")
    if "-" in raw:
        return raw
    for quote in ("USDT", "USDC", "USD"):
        if raw.endswith(quote) and len(raw) > len(quote):
            return f"{raw[:-len(quote)]}-{quote}"
    return raw


_SYMBOL_RULES_CACHE: dict[str, tuple[float, SymbolRules]] = {}


class BingXSwapAdapter(ExchangeAdapter):
    """Read-only BingX USDT-M perpetual adapter."""

    BASE_URL = "https://open-api.bingx.com"

    def __init__(
        self,
        credentials: ExchangeCredentials,
        *,
        recv_window: int = 5000,
        timeout_seconds: float = 10.0,
        connect_timeout_seconds: float | None = None,
        read_timeout_seconds: float | None = None,
        max_attempts: int = 3,
        retry_backoff_seconds: float = 0.35,
        symbol_cache_ttl_seconds: float = 300.0,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self.credentials = credentials
        self.recv_window = max(1000, int(recv_window))
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.connect_timeout_seconds = max(0.5, float(connect_timeout_seconds or min(self.timeout_seconds, 5.0)))
        self.read_timeout_seconds = max(0.5, float(read_timeout_seconds or self.timeout_seconds))
        self.max_attempts = max(1, min(int(max_attempts), 5))
        self.retry_backoff_seconds = max(0.0, float(retry_backoff_seconds))
        self.symbol_cache_ttl_seconds = max(0.0, float(symbol_cache_ttl_seconds))
        self.base_url = self.BASE_URL
        self._session = session
        self._owns_session = session is None

    @property
    def configured(self) -> bool:
        return self.credentials.configured

    async def _client(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(
                total=None,
                connect=self.connect_timeout_seconds,
                sock_connect=self.connect_timeout_seconds,
                sock_read=self.read_timeout_seconds,
            ))
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        if self._owns_session and self._session is not None and not self._session.closed:
            await self._session.close()

    async def _request_once(self, path: str, *, params: Mapping[str, Any] | None = None, signed: bool = False) -> Any:
        payload = {key: value for key, value in (params or {}).items() if value is not None}
        headers = {"Accept": "application/json", "User-Agent": "LiquidityVisionBot/9.8.7"}
        if signed:
            if not self.configured:
                raise ExchangeConfigurationError("BINGX_API_KEY and BINGX_API_SECRET are required")
            payload.setdefault("timestamp", int(time.time() * 1000))
            payload.setdefault("recvWindow", self.recv_window)
            query = urlencode(sorted(payload.items()))
            payload["signature"] = hmac.new(
                self.credentials.api_secret.encode(), query.encode(), hashlib.sha256
            ).hexdigest()
            headers["X-BX-APIKEY"] = self.credentials.api_key

        session = await self._client()
        try:
            async with session.get(f"{self.base_url}{path}", params=payload, headers=headers) as response:
                raw = await response.text()
                status = response.status
        except asyncio.TimeoutError as exc:
            raise ExchangeTimeoutError("BingX request timed out") from exc
        except aiohttp.ClientError as exc:
            raise ExchangeRequestError(f"BingX transport error: {type(exc).__name__}") from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            preview = " ".join(raw.strip().split())[:160] or "empty response"
            raise ExchangeResponseError(f"BingX non-JSON response HTTP {status}: {preview}") from exc

        code = str(data.get("code", "0")) if isinstance(data, dict) else ""
        message = str(data.get("msg") or data.get("message") or "remote error") if isinstance(data, dict) else str(data)
        if status == 429 or code in {"100410", "100421", "101209"}:
            raise ExchangeRateLimitError(f"BingX rate limited ({code}): {message[:200]}")
        if status >= 500:
            raise ExchangeRequestError(f"BingX temporary API error HTTP {status} ({code})")
        if status >= 400 or code not in {"0", ""}:
            if status in {401, 403} or code in {"100001", "100202", "100413", "100414"}:
                raise ExchangeAuthenticationError(f"BingX authentication failed ({code}): {message[:240]}")
            raise ExchangeRequestError(f"BingX API error {status} ({code}): {message[:240]}")
        return data.get("data", data)

    async def _request(self, path: str, *, params: Mapping[str, Any] | None = None, signed: bool = False) -> Any:
        last_error: ExchangeRequestError | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                return await self._request_once(path, params=params, signed=signed)
            except (ExchangeTimeoutError, ExchangeRateLimitError, ExchangeResponseError, ExchangeRequestError) as exc:
                if isinstance(exc, (ExchangeAuthenticationError, ExchangeConfigurationError)):
                    raise
                last_error = exc
                if attempt >= self.max_attempts:
                    break
                delay = self.retry_backoff_seconds * (2 ** (attempt - 1))
                if delay:
                    await asyncio.sleep(delay)
        assert last_error is not None
        raise ExchangeRequestError(f"{last_error} after {self.max_attempts} attempts") from last_error

    async def health(self) -> ExchangeHealth:
        started = time.perf_counter()
        try:
            data = await self._request("/openApi/swap/v2/server/time")
            latency = (time.perf_counter() - started) * 1000
            server_time = int(data.get("serverTime") or data.get("time") or 0) if isinstance(data, dict) else 0
            if not self.configured:
                return ExchangeHealth(ExchangeName.BINGX, True, False, self.credentials.testnet,
                                      round(latency, 2), server_time or None, "credentials_not_configured",
                                      ExchangeStatus.PUBLIC_ONLY, self.base_url)
            await self._request("/openApi/swap/v3/user/balance", signed=True)
            return ExchangeHealth(ExchangeName.BINGX, True, True, self.credentials.testnet,
                                  round(latency, 2), server_time or None, None,
                                  ExchangeStatus.CONNECTED, self.base_url)
        except ExchangeAuthenticationError as exc:
            return ExchangeHealth(ExchangeName.BINGX, True, False, self.credentials.testnet,
                                  status=ExchangeStatus.AUTH_FAILED, error=str(exc), endpoint=self.base_url)
        except ExchangeRequestError as exc:
            return ExchangeHealth(ExchangeName.BINGX, False, False, self.credentials.testnet,
                                  status=ExchangeStatus.UNAVAILABLE, error=str(exc), endpoint=self.base_url)

    async def balances(self) -> list[ExchangeBalance]:
        data = await self._request("/openApi/swap/v3/user/balance", signed=True)
        rows = data.get("balance", data) if isinstance(data, dict) else data
        rows = rows if isinstance(rows, list) else [rows]
        return [ExchangeBalance(
            asset=str(item.get("asset") or item.get("currency") or "USDT"),
            wallet_balance=_decimal(item.get("balance") or item.get("equity")),
            available_balance=_decimal(item.get("availableMargin") or item.get("availableBalance")),
            unrealized_pnl=_decimal(item.get("unrealizedProfit")),
        ) for item in rows if isinstance(item, dict) and _decimal(item.get("balance") or item.get("equity")) != 0]

    async def positions(self) -> list[ExchangePosition]:
        data = await self._request("/openApi/swap/v2/user/positions", signed=True)
        rows = data if isinstance(data, list) else data.get("positions", [])
        result: list[ExchangePosition] = []
        for item in rows:
            quantity = _decimal(item.get("positionAmt") or item.get("positionAmount"))
            if quantity == 0:
                continue
            side = str(item.get("positionSide") or ("LONG" if quantity > 0 else "SHORT")).upper()
            liquidation = _decimal(item.get("liquidationPrice"))
            result.append(ExchangePosition(
                symbol=str(item.get("symbol") or ""), side=side, quantity=abs(quantity),
                entry_price=_decimal(item.get("avgPrice") or item.get("entryPrice")),
                mark_price=_decimal(item.get("markPrice")), unrealized_pnl=_decimal(item.get("unrealizedProfit")),
                leverage=int(_decimal(item.get("leverage"))),
                liquidation_price=liquidation if liquidation > 0 else None,
            ))
        return result

    async def open_orders(self, symbol: str | None = None) -> list[ExchangeOrder]:
        data = await self._request("/openApi/swap/v2/trade/openOrders",
                                   params={"symbol": _symbol(symbol) if symbol else None}, signed=True)
        rows = data.get("orders", data) if isinstance(data, dict) else data
        return [ExchangeOrder(
            order_id=str(item.get("orderId") or ""), symbol=str(item.get("symbol") or ""),
            side=str(item.get("side") or "").upper(), order_type=str(item.get("type") or "").upper(),
            status=str(item.get("status") or "").upper(), quantity=_decimal(item.get("origQty") or item.get("quantity")),
            executed_quantity=_decimal(item.get("executedQty")),
            price=_decimal(item.get("price")) if _decimal(item.get("price")) > 0 else None,
            stop_price=_decimal(item.get("stopPrice")) if _decimal(item.get("stopPrice")) > 0 else None,
            reduce_only=bool(item.get("reduceOnly")),
        ) for item in (rows or [])]

    async def symbol_rules(self, symbol: str) -> SymbolRules:
        normalized = _symbol(symbol)
        cache_key = f"{self.base_url}|{normalized}"
        now = time.monotonic()
        cached = _SYMBOL_RULES_CACHE.get(cache_key)
        if cached and now - cached[0] <= self.symbol_cache_ttl_seconds:
            return cached[1]
        data = await self._request("/openApi/swap/v2/quote/contracts")
        rows = data if isinstance(data, list) else data.get("contracts", [])
        item = next((row for row in rows if str(row.get("symbol", "")).upper() == normalized), None)
        if not item:
            raise ExchangeRequestError(f"BingX symbol {normalized} was not found")
        base, _, quote = normalized.partition("-")
        price_precision = int(item.get("pricePrecision") or 0)
        quantity_precision = int(item.get("quantityPrecision") or 0)
        rules = SymbolRules(
            symbol=normalized, status=str(item.get("status") or "TRADING").lower(),
            base_asset=str(item.get("asset") or base), quote_asset=str(item.get("currency") or quote or "USDT"),
            price_tick=_decimal(item.get("tickSize") or Decimal(1).scaleb(-price_precision)),
            quantity_step=_decimal(item.get("stepSize") or Decimal(1).scaleb(-quantity_precision)),
            min_quantity=_decimal(item.get("minQty") or item.get("tradeMinQuantity") or item.get("size")),
            min_notional=_decimal(item.get("minNotional")) if _decimal(item.get("minNotional")) > 0 else None,
            raw=dict(item),
        )
        if self.symbol_cache_ttl_seconds > 0:
            _SYMBOL_RULES_CACHE[cache_key] = (now, rules)
        return rules
