from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
import random
import re
from decimal import Decimal, ROUND_DOWN
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
    ExchangeOrderRejectedError,
    ExchangeTimestampError,
)
from services.exchanges.models import (
    ExchangeAccountInfo,
    ExchangeBalance,
    ExchangeCapabilities,
    ExchangeCapability,
    ExchangeCredentials,
    ExchangeHealth,
    ExchangeName,
    ExchangeOrder,
    ExchangeOrderRequest,
    ExchangeFill,
    ExchangePosition,
    ExchangeRateLimits,
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


def bingx_client_order_id(value: str) -> str:
    """Return a deterministic BingX-compliant lowercase alphanumeric identity."""
    cleaned = re.sub(r"[^a-zA-Z0-9]", "", value).lower()
    if cleaned and len(cleaned) <= 40:
        return cleaned
    return "lv" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:38]


def _round_down(value: Decimal, step: Decimal) -> Decimal:
    if value <= 0 or step <= 0:
        raise ExchangeConfigurationError("BingX numeric value and precision step must be positive")
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def _plain(value: Decimal) -> str:
    return format(value, "f")


_SYMBOL_RULES_CACHE: dict[str, tuple[float, SymbolRules]] = {}


class BingXSwapAdapter(ExchangeAdapter):
    """Normalized BingX USDT-M perpetual adapter for prod-live and prod-vst."""

    BASE_URL = "https://open-api.bingx.com"
    VST_URL = "https://open-api-vst.bingx.com"
    ADAPTER_VERSION = "9.9.11"

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
        self.environment = "prod-vst" if credentials.testnet else "prod-live"
        self.base_url = self.VST_URL if credentials.testnet else self.BASE_URL
        self._session = session
        self._owns_session = session is None
        self._time_offset_ms = 0
        self._last_rate_limit: ExchangeRateLimits = ExchangeRateLimits()
        self._request_semaphore = asyncio.Semaphore(8)
        self._max_leverage_by_symbol: dict[str, int] = {}

    def capabilities(self) -> ExchangeCapabilities:
        return ExchangeCapabilities(frozenset({
            ExchangeCapability.ACCOUNT_SYNC, ExchangeCapability.BALANCES,
            ExchangeCapability.SYMBOL_RULES, ExchangeCapability.LEVERAGE,
            ExchangeCapability.MARGIN_MODE, ExchangeCapability.PLACE_ORDER,
            ExchangeCapability.CANCEL_ORDER, ExchangeCapability.QUERY_ORDER,
            ExchangeCapability.QUERY_BY_CLIENT_ID, ExchangeCapability.OPEN_ORDERS,
            ExchangeCapability.FILLS, ExchangeCapability.POSITIONS,
            ExchangeCapability.REDUCE_ONLY, ExchangeCapability.STOP_LOSS,
            ExchangeCapability.TAKE_PROFIT, ExchangeCapability.SERVER_TIME,
            ExchangeCapability.RATE_LIMITS,
        }))

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

    @staticmethod
    def sign_params(params: Mapping[str, Any], secret: str) -> tuple[str, str]:
        clean = {str(key): str(value) for key, value in params.items() if value is not None}
        forbidden = re.compile(r"[&=?#\r\n]")
        if any(forbidden.search(key) or forbidden.search(value) for key, value in clean.items()):
            raise ExchangeConfigurationError("BingX request parameter contains a forbidden character")
        query = urlencode(sorted(clean.items()))
        signature = hmac.new(secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()
        return query, signature

    def _timestamp_ms(self) -> int:
        return int(time.time() * 1000) + self._time_offset_ms

    async def _request_once(self, path: str, *, params: Mapping[str, Any] | None = None, signed: bool = False, method: str = "GET") -> Any:
        payload = {key: value for key, value in (params or {}).items() if value is not None}
        headers = {"Accept": "application/json", "User-Agent": "LiquidityVisionBot/9.9.11"}
        request_params: list[tuple[str, Any]] = sorted(payload.items())
        body: str | None = None
        if signed:
            if not self.configured:
                raise ExchangeConfigurationError("BINGX_API_KEY and BINGX_API_SECRET are required")
            payload.setdefault("timestamp", self._timestamp_ms())
            payload.setdefault("recvWindow", self.recv_window)
            query, signature = self.sign_params(payload, self.credentials.api_secret)
            signed_query = f"{query}&signature={signature}"
            request_params = [] if method.upper() == "POST" else list(sorted(payload.items())) + [("signature", signature)]
            body = signed_query if method.upper() == "POST" else None
            headers["X-BX-APIKEY"] = self.credentials.api_key
            if method.upper() == "POST":
                headers["Content-Type"] = "application/x-www-form-urlencoded"

        session = await self._client()
        try:
            async with self._request_semaphore:
                async with session.request(method.upper(), f"{self.base_url}{path}", params=request_params,
                                           data=body, headers=headers) as response:
                    raw = await response.text()
                    status = response.status
                    remaining = response.headers.get("X-RateLimit-Remaining")
                    reset = response.headers.get("X-RateLimit-Reset")
                    self._last_rate_limit = ExchangeRateLimits(
                        int(remaining) if remaining and remaining.isdigit() else None,
                        int(reset) if reset and reset.isdigit() else None,
                    )
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
            if "timestamp" in message.lower() or "recvwindow" in message.lower():
                raise ExchangeTimestampError("BingX request timestamp is outside the accepted window")
            if status in {401, 403} or code in {"100001", "100202", "100413", "100414"}:
                raise ExchangeAuthenticationError(f"BingX authentication failed ({code}): {message[:240]}")
            if code in {"109400", "110407", "110410", "110421"}:
                raise ExchangeOrderRejectedError(f"BingX rejected order ({code}): {message[:240]}")
            raise ExchangeRequestError(f"BingX API error {status} ({code}): {message[:240]}")
        return data.get("data", data)

    async def _request(self, path: str, *, params: Mapping[str, Any] | None = None, signed: bool = False, method: str = "GET") -> Any:
        last_error: ExchangeRequestError | None = None
        attempts = self.max_attempts if method.upper() == "GET" else 1
        for attempt in range(1, attempts + 1):
            try:
                return await self._request_once(path, params=params, signed=signed, method=method)
            except (ExchangeTimeoutError, ExchangeRateLimitError, ExchangeResponseError, ExchangeRequestError) as exc:
                if isinstance(exc, (ExchangeAuthenticationError, ExchangeConfigurationError)):
                    raise
                last_error = exc
                if attempt >= attempts:
                    break
                if isinstance(exc, ExchangeTimestampError):
                    data = await self._request_once("/openApi/swap/v2/server/time")
                    server_ms = int(data.get("serverTime") or data.get("time") or 0) if isinstance(data, dict) else 0
                    if server_ms:
                        self._time_offset_ms = server_ms - int(time.time() * 1000)
                delay = self.retry_backoff_seconds * (2 ** (attempt - 1)) + random.uniform(0, self.retry_backoff_seconds)
                if delay:
                    await asyncio.sleep(delay)
        assert last_error is not None
        if attempts == 1:
            raise last_error
        raise ExchangeRequestError(f"{last_error} after {attempts} attempts") from last_error

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

    async def server_time(self) -> int:
        data = await self._request("/openApi/swap/v2/server/time")
        server_ms = int(data.get("serverTime") or data.get("time") or 0) if isinstance(data, dict) else 0
        if server_ms <= 0:
            raise ExchangeResponseError("BingX server time response was invalid")
        self._time_offset_ms = server_ms - int(time.time() * 1000)
        return server_ms

    async def rate_limits(self) -> ExchangeRateLimits:
        return self._last_rate_limit

    async def account_info(self) -> ExchangeAccountInfo:
        if not self.configured:
            raise ExchangeConfigurationError("BingX credentials are required")
        mode_data = await self._request("/openApi/swap/v1/positionSide/dual", signed=True)
        dual = mode_data.get("dualSidePosition") if isinstance(mode_data, dict) else None
        if isinstance(dual, str):
            dual = dual.lower() == "true"
        if not isinstance(dual, bool):
            raise ExchangeResponseError("BingX position mode could not be established")
        await self.balances()  # Authenticated trading-account scope proof.
        return ExchangeAccountInfo(
            trading_enabled=True, withdrawal_enabled=None,
            position_mode="HEDGE" if dual else "ONE_WAY", environment=self.environment,
        )

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
            raw_side = str(item.get("positionSide") or "").upper()
            side = raw_side if raw_side in {"LONG", "SHORT"} else ("LONG" if quantity > 0 else "SHORT")
            liquidation = _decimal(item.get("liquidationPrice"))
            result.append(ExchangePosition(
                symbol=str(item.get("symbol") or ""), side=side, quantity=abs(quantity),
                entry_price=_decimal(item.get("avgPrice") or item.get("entryPrice")),
                mark_price=_decimal(item.get("markPrice")), unrealized_pnl=_decimal(item.get("unrealizedProfit")),
                leverage=int(_decimal(item.get("leverage"))),
                liquidation_price=liquidation if liquidation > 0 else None,
                margin_mode=str(item.get("marginType") or "").upper() or None,
                position_id=str(item.get("positionId")) if item.get("positionId") is not None else None,
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
            reduce_only=str(item.get("reduceOnly", "false")).lower() in {"1", "true", "yes"},
            client_order_id=str(item.get("clientOrderId") or item.get("clientOrderID") or "") or None,
            average_price=_decimal(item.get("avgPrice")) if _decimal(item.get("avgPrice")) > 0 else None,
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
            min_notional=_decimal(item.get("minNotional") or item.get("tradeMinUSDT") or item.get("tradeMinNotional")) or None,
            max_quantity=_decimal(item.get("maxQty") or item.get("tradeMaxQuantity") or item.get("maxQuantity")) or None,
            max_leverage=int(_decimal(item.get("maxLongLeverage") or item.get("maxLeverage"))) or None,
        )
        if self.symbol_cache_ttl_seconds > 0:
            _SYMBOL_RULES_CACHE[cache_key] = (now, rules)
        return rules

    @staticmethod
    def _parse_order(item: Mapping[str, Any]) -> ExchangeOrder:
        return ExchangeOrder(
            order_id=str(item.get("orderId") or item.get("orderID") or ""),
            symbol=str(item.get("symbol") or ""),
            side=str(item.get("side") or "").upper(),
            order_type=str(item.get("type") or item.get("orderType") or "").upper(),
            status=str(item.get("status") or "NEW").upper(),
            quantity=_decimal(item.get("origQty") or item.get("quantity")),
            executed_quantity=_decimal(item.get("executedQty") or item.get("executedQuantity")),
            price=_decimal(item.get("price")) if _decimal(item.get("price")) > 0 else None,
            stop_price=_decimal(item.get("stopPrice")) if _decimal(item.get("stopPrice")) > 0 else None,
            reduce_only=str(item.get("reduceOnly", "false")).lower() in {"1", "true", "yes"},
            client_order_id=str(item.get("clientOrderId") or item.get("clientOrderID") or "") or None,
            average_price=_decimal(item.get("avgPrice")) if _decimal(item.get("avgPrice")) > 0 else None,
        )

    async def margin_mode(self, symbol: str) -> str:
        data = await self._request("/openApi/swap/v2/trade/marginType",
                                   params={"symbol": _symbol(symbol)}, signed=True)
        value = str(data.get("marginType") if isinstance(data, dict) else data).upper()
        if value not in {"ISOLATED", "CROSSED"}:
            raise ExchangeResponseError("BingX margin mode could not be established")
        return value

    async def set_margin_mode(self, symbol: str, margin_mode: str) -> None:
        value = margin_mode.upper()
        if value not in {"ISOLATED", "CROSSED"}:
            raise ExchangeConfigurationError("BingX margin mode must be ISOLATED or CROSSED")
        if await self.margin_mode(symbol) == value:
            return
        await self._request("/openApi/swap/v2/trade/marginType",
                            params={"symbol": _symbol(symbol), "marginType": value},
                            signed=True, method="POST")

    async def current_leverage(self, symbol: str) -> dict[str, int]:
        data = await self._request("/openApi/swap/v2/trade/leverage",
                                   params={"symbol": _symbol(symbol)}, signed=True)
        row = data if isinstance(data, dict) else {}
        result = {}
        for side, keys in {"LONG": ("longLeverage", "leverage"), "SHORT": ("shortLeverage", "leverage")}.items():
            raw = next((row.get(key) for key in keys if row.get(key) is not None), None)
            if raw is not None:
                result[side] = int(_decimal(raw))
        if not result:
            raise ExchangeResponseError("BingX leverage could not be established")
        maxima = [int(_decimal(row[key])) for key in ("maxLongLeverage", "maxShortLeverage")
                  if row.get(key) is not None and _decimal(row.get(key)) > 0]
        if maxima:
            self._max_leverage_by_symbol[_symbol(symbol)] = min(maxima)
        return result

    async def set_leverage(self, symbol: str, leverage: int) -> None:
        rules = await self.symbol_rules(symbol)
        if leverage < 1 or (rules.max_leverage and leverage > rules.max_leverage):
            raise ExchangeConfigurationError("BingX leverage is outside symbol limits")
        current = await self.current_leverage(symbol)
        authoritative_max = self._max_leverage_by_symbol.get(_symbol(symbol))
        if authoritative_max and leverage > authoritative_max:
            raise ExchangeConfigurationError("BingX leverage exceeds the account/symbol maximum")
        for side in ("LONG", "SHORT"):
            if current.get(side) == leverage:
                continue
            await self._request("/openApi/swap/v2/trade/leverage",
                                params={"symbol": _symbol(symbol), "side": side, "leverage": leverage},
                                signed=True, method="POST")

    async def normalize_order(self, request: ExchangeOrderRequest) -> ExchangeOrderRequest:
        rules = await self.symbol_rules(request.symbol)
        quantity = _round_down(request.quantity, rules.quantity_step)
        if quantity < rules.min_quantity:
            raise ExchangeOrderRejectedError("BingX order quantity is below the symbol minimum")
        if rules.max_quantity is not None and quantity > rules.max_quantity:
            raise ExchangeOrderRejectedError("BingX order quantity exceeds the symbol maximum")
        price = _round_down(request.price, rules.price_tick) if request.price is not None else None
        if rules.min_notional is not None:
            if price is None:
                raise ExchangeOrderRejectedError("BingX minimum notional validation requires a reference price")
            if quantity * price < rules.min_notional:
                raise ExchangeOrderRejectedError("BingX order notional is below the symbol minimum")
        order_type = request.order_type.upper()
        if order_type not in {"MARKET", "LIMIT"}:
            raise ExchangeOrderRejectedError("BingX adapter supports MARKET and LIMIT economic intents")
        if order_type == "LIMIT" and price is None:
            raise ExchangeOrderRejectedError("BingX limit order requires a price")
        mode = (await self.account_info()).position_mode
        position_side = (request.position_side or "").upper()
        if request.reduce_only and mode == "HEDGE" and not position_side:
            expected_position = "LONG" if request.side.upper() == "SELL" else "SHORT"
            positions = [item for item in await self.positions()
                         if _symbol(item.symbol) == rules.symbol and item.side == expected_position and item.quantity > 0]
            if len(positions) != 1:
                raise ExchangeOrderRejectedError("BingX hedge close positionSide is ambiguous")
            position_side = expected_position
        if mode == "HEDGE" and position_side not in {"LONG", "SHORT"}:
            raise ExchangeOrderRejectedError("BingX hedge mode requires LONG or SHORT positionSide")
        if mode == "ONE_WAY" and position_side not in {"", "BOTH"}:
            raise ExchangeOrderRejectedError("BingX one-way mode requires BOTH positionSide")
        if request.reduce_only and mode == "HEDGE":
            expected_side = "SELL" if position_side == "LONG" else "BUY"
            if request.side.upper() != expected_side:
                raise ExchangeOrderRejectedError("BingX reduce-only side would increase hedge-mode exposure")
        if request.reduce_only:
            expected_position = "LONG" if request.side.upper() == "SELL" else "SHORT"
            positions = [item for item in await self.positions()
                         if _symbol(item.symbol) == rules.symbol and item.side == expected_position and item.quantity > 0]
            if len(positions) != 1:
                raise ExchangeOrderRejectedError("BingX safe close requires one resolved matching position")
            quantity = min(quantity, positions[0].quantity)
        if request.leverage < 1 or (rules.max_leverage and request.leverage > rules.max_leverage):
            raise ExchangeOrderRejectedError("BingX leverage is outside symbol limits")
        stop_loss = _round_down(request.stop_loss, rules.price_tick) if request.stop_loss is not None else None
        take_profit = _round_down(request.take_profit, rules.price_tick) if request.take_profit is not None else None
        return ExchangeOrderRequest(
            symbol=rules.symbol, side=request.side.upper(), order_type=order_type, quantity=quantity,
            client_order_id=bingx_client_order_id(request.client_order_id), price=price,
            leverage=request.leverage, margin_mode=request.margin_mode, reduce_only=request.reduce_only,
            stop_loss=stop_loss, take_profit=take_profit,
            position_side=position_side or "BOTH", working_type=request.working_type,
        )

    async def place_order(self, request: ExchangeOrderRequest) -> ExchangeOrder:
        normalized = await self.normalize_order(request)
        if normalized.margin_mode:
            await self.set_margin_mode(normalized.symbol, normalized.margin_mode)
        await self.set_leverage(normalized.symbol, normalized.leverage)
        payload: dict[str, Any] = {
            "symbol": normalized.symbol, "side": normalized.side,
            "positionSide": normalized.position_side, "type": normalized.order_type,
            "quantity": _plain(normalized.quantity),
            "price": _plain(normalized.price) if normalized.order_type == "LIMIT" and normalized.price is not None else None,
            "timeInForce": "GTC" if normalized.order_type == "LIMIT" else None,
            "clientOrderId": normalized.client_order_id,
        }
        # BingX hedge mode rejects reduceOnly entirely. positionSide is the safe-close primitive there.
        if normalized.reduce_only and normalized.position_side == "BOTH":
            payload["reduceOnly"] = "true"
        if normalized.stop_loss is not None:
            payload["stopLoss"] = json.dumps({"type": "STOP_MARKET", "stopPrice": _plain(normalized.stop_loss),
                                               "workingType": normalized.working_type}, separators=(",", ":"))
        if normalized.take_profit is not None:
            payload["takeProfit"] = json.dumps({"type": "TAKE_PROFIT_MARKET", "stopPrice": _plain(normalized.take_profit),
                                                 "workingType": normalized.working_type}, separators=(",", ":"))
        data = await self._request("/openApi/swap/v2/trade/order", params=payload, signed=True, method="POST")
        item = data.get("order", data) if isinstance(data, dict) else {}
        order = self._parse_order(item)
        if not order.order_id:
            raise ExchangeResponseError("BingX accepted response did not contain an order identity")
        return order

    async def query_order(self, *, symbol: str, order_id: str) -> ExchangeOrder | None:
        data = await self._request("/openApi/swap/v2/trade/order",
                                   params={"symbol": _symbol(symbol), "orderId": order_id}, signed=True)
        item = data.get("order", data) if isinstance(data, dict) else {}
        return self._parse_order(item) if item else None

    async def query_order_by_client_id(self, *, symbol: str, client_order_id: str) -> ExchangeOrder | None:
        data = await self._request("/openApi/swap/v2/trade/order", params={
            "symbol": _symbol(symbol), "clientOrderId": bingx_client_order_id(client_order_id),
        }, signed=True)
        item = data.get("order", data) if isinstance(data, dict) else {}
        return self._parse_order(item) if item else None

    async def cancel_order(self, *, symbol: str, order_id: str) -> ExchangeOrder:
        current = await self.query_order(symbol=symbol, order_id=order_id)
        if current and current.status in {"CANCELLED", "CANCELED", "FILLED"}:
            return current
        data = await self._request("/openApi/swap/v2/trade/order",
                                   params={"symbol": _symbol(symbol), "orderId": order_id},
                                   signed=True, method="DELETE")
        item = data.get("order", data) if isinstance(data, dict) else {}
        order = self._parse_order(item) if item else await self.query_order(symbol=symbol, order_id=order_id)
        if order is None:
            raise ExchangeResponseError("BingX cancellation truth could not be established")
        return order

    async def fills(self, *, symbol: str, order_id: str | None = None) -> list[ExchangeFill]:
        end_ms = self._timestamp_ms()
        data = await self._request("/openApi/swap/v2/trade/allFillOrders",
                                   params={"tradingUnit": "COIN", "startTs": end_ms - 7 * 24 * 60 * 60 * 1000,
                                           "endTs": end_ms, "orderId": order_id}, signed=True)
        rows = data.get("fillOrders", data.get("orders", data)) if isinstance(data, dict) else data
        result: list[ExchangeFill] = []
        for item in rows or []:
            fill_id = str(item.get("tradeId") or item.get("fillId") or item.get("id") or "")
            remote_order = str(item.get("orderId") or item.get("orderID") or "")
            if not fill_id or not remote_order:
                continue
            if order_id is not None and remote_order != str(order_id):
                continue
            if item.get("symbol") and _symbol(str(item.get("symbol"))) != _symbol(symbol):
                continue
            result.append(ExchangeFill(
                fill_id=fill_id, order_id=remote_order,
                client_order_id=str(item.get("clientOrderId") or "") or None,
                symbol=str(item.get("symbol") or _symbol(symbol)), side=str(item.get("side") or "").upper(),
                quantity=_decimal(item.get("qty") or item.get("quantity") or item.get("executedQty")),
                price=_decimal(item.get("price") or item.get("avgPrice")),
                commission=abs(_decimal(item.get("commission") or item.get("fee"))),
                commission_asset=str(item.get("commissionAsset") or item.get("feeAsset") or "") or None,
                filled_at_ms=int(item.get("time") or item.get("timestamp") or 0) or None,
                realized_pnl=_decimal(item.get("realizedPnl") or item.get("profit")),
            ))
        return result

    async def create_demo_order(
        self, *, symbol: str, side: str, order_type: str, quantity: Decimal,
        price: Decimal | None = None, leverage: int = 1, reduce_only: bool = False,
        position_side: str | None = None, client_order_id: str | None = None,
    ) -> ExchangeOrder:
        if not self.credentials.testnet:
            raise ExchangeConfigurationError("BingX demo execution refuses non-demo credentials")
        normalized = _symbol(symbol)
        side = side.upper()
        position_side = (position_side or ("LONG" if side == "BUY" else "SHORT")).upper()
        await self._request(
            "/openApi/swap/v2/trade/leverage",
            params={"symbol": normalized, "side": position_side, "leverage": leverage},
            signed=True, method="POST",
        )
        payload = {
            "symbol": normalized, "side": side, "positionSide": position_side,
            "type": order_type.upper(), "quantity": str(quantity),
            "price": str(price) if price is not None else None,
            "timeInForce": "GTC" if order_type.upper() == "LIMIT" else None,
            "clientOrderID": client_order_id,
        }
        # BingX hedge mode rejects the reduceOnly field even when its value is
        # false (error 109400).  Omit it for normal opening orders and send it
        # only when the caller explicitly requests a reduce-only close.
        if reduce_only:
            payload["reduceOnly"] = "true"
        data = await self._request("/openApi/swap/v2/trade/order", params=payload, signed=True, method="POST")
        item = data.get("order", data) if isinstance(data, dict) else {}
        return self._parse_order(item)

    async def cancel_demo_order(self, *, symbol: str, order_id: str) -> ExchangeOrder:
        if not self.credentials.testnet:
            raise ExchangeConfigurationError("BingX demo cancellation refuses non-demo credentials")
        data = await self._request(
            "/openApi/swap/v2/trade/order",
            params={"symbol": _symbol(symbol), "orderId": order_id}, signed=True, method="DELETE",
        )
        item = data.get("order", data) if isinstance(data, dict) else {}
        order = self._parse_order(item)
        if not order.order_id:
            order = ExchangeOrder(order_id, _symbol(symbol), "", "", "CANCELLED", Decimal("0"), Decimal("0"))
        return order

    async def demo_order_status(self, *, symbol: str, order_id: str) -> ExchangeOrder:
        data = await self._request(
            "/openApi/swap/v2/trade/order",
            params={"symbol": _symbol(symbol), "orderId": order_id}, signed=True, method="GET",
        )
        item = data.get("order", data) if isinstance(data, dict) else {}
        return self._parse_order(item)
