from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
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


def _instrument_id(symbol: str) -> str:
    raw = symbol.strip().upper()
    if "-" in raw:
        return raw
    for quote in ("USDT", "USDC", "USD"):
        if raw.endswith(quote) and len(raw) > len(quote):
            return f"{raw[:-len(quote)]}-{quote}-SWAP"
    return raw


_SYMBOL_RULES_CACHE: dict[str, tuple[float, SymbolRules]] = {}


class OkxV5Adapter(ExchangeAdapter):
    """Read-only OKX V5 perpetual-swap adapter with demo-trading support."""

    BASE_URL = "https://www.okx.com"

    def __init__(
        self,
        credentials: ExchangeCredentials,
        *,
        passphrase: str = "",
        timeout_seconds: float = 10.0,
        connect_timeout_seconds: float | None = None,
        read_timeout_seconds: float | None = None,
        max_attempts: int = 3,
        retry_backoff_seconds: float = 0.35,
        symbol_cache_ttl_seconds: float = 300.0,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self.credentials = credentials
        self.passphrase = passphrase.strip()
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
        return self.credentials.configured and bool(self.passphrase)

    async def _client(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(
                    total=None,
                    connect=self.connect_timeout_seconds,
                    sock_connect=self.connect_timeout_seconds,
                    sock_read=self.read_timeout_seconds,
                )
            )
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        if self._owns_session and self._session is not None and not self._session.closed:
            await self._session.close()

    async def _request_once(self, path: str, *, params: Mapping[str, Any] | None = None, signed: bool = False) -> Any:
        payload = {key: value for key, value in (params or {}).items() if value is not None}
        query = urlencode(payload, doseq=True)
        request_path = path + (f"?{query}" if query else "")
        headers = {"Accept": "application/json", "User-Agent": "LiquidityVisionBot/9.8.7"}
        if self.credentials.testnet:
            headers["x-simulated-trading"] = "1"
        if signed:
            if not self.configured:
                raise ExchangeConfigurationError(
                    "OKX_API_KEY, OKX_API_SECRET and OKX_API_PASSPHRASE are required"
                )
            timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            prehash = f"{timestamp}GET{request_path}"
            signature = base64.b64encode(
                hmac.new(self.credentials.api_secret.encode(), prehash.encode(), hashlib.sha256).digest()
            ).decode()
            headers.update({
                "OK-ACCESS-KEY": self.credentials.api_key,
                "OK-ACCESS-SIGN": signature,
                "OK-ACCESS-TIMESTAMP": timestamp,
                "OK-ACCESS-PASSPHRASE": self.passphrase,
            })

        session = await self._client()
        try:
            async with session.get(f"{self.base_url}{path}", params=payload, headers=headers) as response:
                raw = await response.text()
        except asyncio.TimeoutError as exc:
            raise ExchangeTimeoutError("OKX request timed out") from exc
        except aiohttp.ClientError as exc:
            raise ExchangeRequestError(f"OKX transport error: {type(exc).__name__}") from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            preview = " ".join(raw.strip().split())[:160] or "empty response"
            raise ExchangeResponseError(
                f"OKX non-JSON response HTTP {response.status}: {preview}"
            ) from exc

        code = str(data.get("code", "0")) if isinstance(data, dict) else ""
        message = data.get("msg") if isinstance(data, dict) else str(data)
        if response.status == 429 or code in {"50011", "50040"}:
            raise ExchangeRateLimitError(f"OKX rate limited ({code}): {str(message or 'slow down')[:200]}")
        if response.status >= 500:
            raise ExchangeRequestError(f"OKX temporary API error HTTP {response.status} ({code})")
        if response.status >= 400 or code != "0":
            safe = str(message or "remote error")[:240]
            if response.status in {401, 403} or code in {"50110", "50111", "50112", "50113", "50114"}:
                raise ExchangeAuthenticationError(f"OKX authentication failed ({code}): {safe}")
            raise ExchangeRequestError(f"OKX API error {response.status} ({code}): {safe}")
        return data.get("data", [])

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
        raise ExchangeRequestError(
            f"{last_error} after {self.max_attempts} attempts"
        ) from last_error

    async def _server_time(self) -> tuple[int, float]:
        started = time.perf_counter()
        rows = await self._request("/api/v5/public/time")
        latency_ms = (time.perf_counter() - started) * 1000
        if not rows:
            raise ExchangeRequestError("OKX server time response was empty")
        return int(rows[0]["ts"]), latency_ms

    async def health(self) -> ExchangeHealth:
        try:
            server_time, latency_ms = await self._server_time()
            if not self.configured:
                return ExchangeHealth(
                    exchange=ExchangeName.OKX, reachable=True, authenticated=False,
                    testnet=self.credentials.testnet, latency_ms=round(latency_ms, 2),
                    server_time_ms=server_time, status=ExchangeStatus.PUBLIC_ONLY,
                    error="credentials_not_configured", endpoint=self.base_url,
                )
            await self._request("/api/v5/account/balance", signed=True)
            return ExchangeHealth(
                exchange=ExchangeName.OKX, reachable=True, authenticated=True,
                testnet=self.credentials.testnet, latency_ms=round(latency_ms, 2),
                server_time_ms=server_time, status=ExchangeStatus.CONNECTED, endpoint=self.base_url,
            )
        except ExchangeAuthenticationError as exc:
            return ExchangeHealth(ExchangeName.OKX, True, False, self.credentials.testnet,
                                  status=ExchangeStatus.AUTH_FAILED, error=str(exc), endpoint=self.base_url)
        except ExchangeConfigurationError as exc:
            return ExchangeHealth(ExchangeName.OKX, True, False, self.credentials.testnet,
                                  status=ExchangeStatus.NOT_CONFIGURED, error=str(exc), endpoint=self.base_url)
        except ExchangeRequestError as exc:
            return ExchangeHealth(ExchangeName.OKX, False, False, self.credentials.testnet,
                                  status=ExchangeStatus.UNAVAILABLE, error=str(exc), endpoint=self.base_url)

    async def balances(self) -> list[ExchangeBalance]:
        rows = await self._request("/api/v5/account/balance", signed=True)
        details = rows[0].get("details", []) if rows else []
        return [
            ExchangeBalance(
                asset=str(item.get("ccy") or ""),
                wallet_balance=_decimal(item.get("eq") or item.get("cashBal")),
                available_balance=_decimal(item.get("availEq") or item.get("availBal")),
                unrealized_pnl=_decimal(item.get("upl")),
            )
            for item in details
            if _decimal(item.get("eq") or item.get("cashBal")) != 0
        ]

    async def positions(self) -> list[ExchangePosition]:
        rows = await self._request("/api/v5/account/positions", params={"instType": "SWAP"}, signed=True)
        result: list[ExchangePosition] = []
        for item in rows:
            quantity = _decimal(item.get("pos"))
            if quantity == 0:
                continue
            side = str(item.get("posSide") or "").upper()
            if side == "NET":
                side = "LONG" if quantity > 0 else "SHORT"
            liquidation = _decimal(item.get("liqPx"))
            result.append(ExchangePosition(
                symbol=str(item.get("instId") or ""), side=side, quantity=abs(quantity),
                entry_price=_decimal(item.get("avgPx")), mark_price=_decimal(item.get("markPx")),
                unrealized_pnl=_decimal(item.get("upl")), leverage=int(_decimal(item.get("lever"))),
                liquidation_price=liquidation if liquidation > 0 else None,
            ))
        return result

    async def open_orders(self, symbol: str | None = None) -> list[ExchangeOrder]:
        params = {"instType": "SWAP", "instId": _instrument_id(symbol) if symbol else None}
        rows = await self._request("/api/v5/trade/orders-pending", params=params, signed=True)
        return [ExchangeOrder(
            order_id=str(item.get("ordId") or ""), symbol=str(item.get("instId") or ""),
            side=str(item.get("side") or "").upper(), order_type=str(item.get("ordType") or "").upper(),
            status=str(item.get("state") or "").upper(), quantity=_decimal(item.get("sz")),
            executed_quantity=_decimal(item.get("accFillSz")),
            price=_decimal(item.get("px")) if _decimal(item.get("px")) > 0 else None,
            stop_price=None, reduce_only=bool(item.get("reduceOnly")),
        ) for item in rows]

    async def symbol_rules(self, symbol: str) -> SymbolRules:
        inst_id = _instrument_id(symbol)
        cache_key = f"{self.base_url}|{self.credentials.testnet}|{inst_id}"
        cached = _SYMBOL_RULES_CACHE.get(cache_key)
        now = time.monotonic()
        if cached and now - cached[0] <= self.symbol_cache_ttl_seconds:
            return cached[1]

        rows = await self._request("/api/v5/public/instruments", params={"instType": "SWAP", "instId": inst_id})
        if not rows:
            raise ExchangeRequestError(f"OKX symbol {inst_id} was not found")
        item = rows[0]
        min_size = _decimal(item.get("minSz"))
        rules = SymbolRules(
            symbol=inst_id, status=str(item.get("state") or ""),
            base_asset=str(item.get("ctValCcy") or item.get("baseCcy") or inst_id.split("-")[0]),
            quote_asset=str(item.get("settleCcy") or item.get("quoteCcy") or "USDT"),
            price_tick=_decimal(item.get("tickSz")), quantity_step=_decimal(item.get("lotSz")),
            min_quantity=min_size, min_notional=None, raw=dict(item),
        )
        if self.symbol_cache_ttl_seconds > 0:
            _SYMBOL_RULES_CACHE[cache_key] = (now, rules)
        return rules
