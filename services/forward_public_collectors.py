"""Credential-free multi-venue WebSocket collectors for the forward lab."""
from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
import time
import uuid
from dataclasses import replace
from typing import Any, Awaitable, Callable

import aiohttp

from services.forward_event_store import (
    AppendOnlyEventStore, EventType, IntegrityStatus, RawMarketEvent, Venue,
)
from services.forward_microstructure_engine import CrossVenueState, MicrostructureFeatureEngine
from services.forward_shadow_lab import ForwardOutcomeLabeler, ForwardShadowEngine


Emit = Callable[[RawMarketEvent], Awaitable[None]]


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def _symbol(value: str) -> str:
    return value.upper().replace("-", "").replace("_", "")


def parse_binance_message(message: dict[str, Any], *, receive_ts_ms: int,
                           connection_id: str) -> list[RawMarketEvent]:
    data = message.get("data", message)
    event = data.get("e")
    symbol = _symbol(data.get("s") or data.get("o", {}).get("s") or "")
    common = dict(
        venue=Venue.BINANCE, market="USD_M_PERPETUAL", symbol=symbol,
        instrument_type="PERPETUAL", receive_ts_ms=receive_ts_ms,
        connection_id=connection_id,
    )
    if event == "aggTrade":
        buyer_maker = bool(data["m"])
        return [RawMarketEvent(
            **common, event_type=EventType.TRADE,
            exchange_ts_ms=int(data.get("T") or data["E"]),
            sequence_start=int(data["a"]), sequence_end=int(data["a"]),
            price=float(data["p"]), quantity=float(data["q"]),
            side="SELL" if buyer_maker else "BUY", is_buyer_maker=buyer_maker,
            payload=data, metadata={"aggressor_semantics": "BUYER_MAKER_FALSE_IS_BUY_AGGRESSOR"},
        )]
    if event == "depthUpdate":
        return [RawMarketEvent(
            **common, event_type=EventType.BOOK_DELTA,
            exchange_ts_ms=int(data.get("T") or data["E"]),
            sequence_start=int(data["U"]), sequence_end=int(data["u"]),
            previous_sequence=int(data["pu"]) if data.get("pu") is not None else None,
            payload=data,
        )]
    if event == "forceOrder":
        order = data["o"]
        price = float(order.get("ap") or order.get("p") or 0)
        return [RawMarketEvent(
            **common, event_type=EventType.LIQUIDATION,
            exchange_ts_ms=int(order.get("T") or data["E"]),
            price=price, quantity=float(order["q"]), side=str(order["S"]).upper(),
            payload=data, metadata={
                "forced_order_side": str(order["S"]).upper(),
                "liquidated_position_side": "LONG" if str(order["S"]).upper() == "SELL" else "SHORT",
            },
        )]
    if event == "markPriceUpdate":
        timestamp = int(data["E"])
        result = [
            RawMarketEvent(**common, event_type=EventType.MARK_PRICE, exchange_ts_ms=timestamp,
                           price=float(data["p"]), payload=data),
            RawMarketEvent(**common, event_type=EventType.INDEX_PRICE, exchange_ts_ms=timestamp,
                           price=float(data["i"]), payload=data),
            RawMarketEvent(**common, event_type=EventType.FUNDING, exchange_ts_ms=timestamp,
                           quantity=float(data.get("r") or 0), payload=data,
                           metadata={"next_funding_time_ms": int(data.get("T") or 0)}),
        ]
        return result
    return []


def parse_okx_message(message: dict[str, Any], *, receive_ts_ms: int,
                      connection_id: str) -> list[RawMarketEvent]:
    arg = message.get("arg", {})
    channel = arg.get("channel")
    instrument = arg.get("instId") or ""
    result: list[RawMarketEvent] = []
    for data in message.get("data", []):
        data_instrument = instrument or data.get("instId") or ""
        symbol = _symbol(str(data_instrument).split("-SWAP")[0])
        common = dict(
            venue=Venue.OKX, market="USDT_SWAP", symbol=symbol,
            instrument_type="PERPETUAL", receive_ts_ms=receive_ts_ms,
            connection_id=connection_id,
        )
        timestamp = int(data.get("ts") or receive_ts_ms)
        if channel in {"trades", "trades-all"}:
            side = str(data.get("side") or "").upper()
            if side not in {"BUY", "SELL"}:
                continue
            result.append(RawMarketEvent(
                **common, event_type=EventType.TRADE, exchange_ts_ms=timestamp,
                sequence_start=int(data["tradeId"]) if str(data.get("tradeId", "")).isdigit() else None,
                sequence_end=int(data["tradeId"]) if str(data.get("tradeId", "")).isdigit() else None,
                price=float(data["px"]), quantity=float(data["sz"]), side=side,
                payload=data, metadata={"aggressor_semantics": "SIDE_IS_TAKER_SIDE"},
            ))
        elif channel in {"books", "books-rpi"}:
            action = str(message.get("action") or "update")
            result.append(RawMarketEvent(
                **common,
                event_type=EventType.BOOK_SNAPSHOT if action == "snapshot" else EventType.BOOK_DELTA,
                exchange_ts_ms=timestamp,
                sequence_start=int(data.get("seqId")) if data.get("seqId") is not None else None,
                sequence_end=int(data.get("seqId")) if data.get("seqId") is not None else None,
                previous_sequence=int(data.get("prevSeqId")) if data.get("prevSeqId") is not None else None,
                # Preserve the exact payload, including deprecated checksum zero.
                # The book engine ignores that sentinel and enforces seq continuity.
                payload=data,
            ))
        elif channel == "mark-price":
            result.append(RawMarketEvent(**common, event_type=EventType.MARK_PRICE,
                                         exchange_ts_ms=timestamp, price=float(data["markPx"]), payload=data))
        elif channel == "index-tickers":
            result.append(RawMarketEvent(**common, event_type=EventType.INDEX_PRICE,
                                         exchange_ts_ms=timestamp, price=float(data["idxPx"]), payload=data))
        elif channel == "open-interest":
            quantity = float(data.get("oiUsd") or data.get("oiCcy") or data.get("oi") or 0)
            result.append(RawMarketEvent(**common, event_type=EventType.OPEN_INTEREST,
                                         exchange_ts_ms=timestamp, quantity=quantity, payload=data,
                                         metadata={"unit": "USD" if data.get("oiUsd") else "SOURCE_NATIVE"}))
        elif channel == "funding-rate":
            result.append(RawMarketEvent(**common, event_type=EventType.FUNDING,
                                         exchange_ts_ms=timestamp, quantity=float(data["fundingRate"]), payload=data,
                                         metadata={"next_funding_time_ms": int(data.get("nextFundingTime") or 0)}))
        elif channel == "liquidation-orders":
            details = data.get("details") or [data]
            for detail in details:
                side = str(detail.get("side") or "").upper()
                price = float(detail.get("bkPx") or detail.get("px") or 0)
                quantity = float(detail.get("sz") or 0)
                if side in {"BUY", "SELL"} and price > 0 and quantity >= 0:
                    result.append(RawMarketEvent(
                        **common, event_type=EventType.LIQUIDATION,
                        exchange_ts_ms=int(detail.get("ts") or timestamp),
                        price=price, quantity=quantity, side=side, payload={"parent": data, "detail": detail},
                        metadata={"liquidated_position_side": "LONG" if side == "SELL" else "SHORT"},
                    ))
    return result


def parse_bingx_message(message: dict[str, Any], *, receive_ts_ms: int,
                        connection_id: str) -> list[RawMarketEvent]:
    channel = str(message.get("dataType") or "")
    data = message.get("data") or {}
    sample = data[0] if isinstance(data, list) and data else data
    if not isinstance(sample, dict):
        return []
    symbol = _symbol(sample.get("s") or channel.split("@")[0])
    timestamp = int(sample.get("T") or sample.get("time") or receive_ts_ms)
    common = dict(
        venue=Venue.BINGX, market="USDT_M_PERPETUAL", symbol=symbol,
        instrument_type="PERPETUAL", receive_ts_ms=receive_ts_ms,
        connection_id=connection_id,
    )
    if channel.endswith("@trade"):
        rows = data if isinstance(data, list) else [data]
        result = []
        for row in rows:
            buyer_maker = bool(row.get("m"))
            result.append(RawMarketEvent(
                **common, event_type=EventType.TRADE,
                exchange_ts_ms=int(row.get("T") or timestamp),
                price=float(row.get("p") or row.get("price")),
                quantity=float(row.get("q") or row.get("qty")),
                side="SELL" if buyer_maker else "BUY", is_buyer_maker=buyer_maker,
                payload=row, metadata={"aggressor_semantics": "BUYER_MAKER_FALSE_IS_BUY_AGGRESSOR"},
            ))
        return result
    if "@depth" in channel:
        return [RawMarketEvent(
            **common, event_type=EventType.BOOK_SNAPSHOT, exchange_ts_ms=timestamp,
            sequence_start=timestamp, sequence_end=timestamp,
            payload=data,
            metadata={"sequence_semantics": "SNAPSHOT_ONLY_NO_DELTA_CONTINUITY"},
        )]
    if channel.endswith("@markPrice"):
        result = [RawMarketEvent(
            **common, event_type=EventType.MARK_PRICE, exchange_ts_ms=timestamp,
            price=float(data.get("p") or data.get("markPrice")), payload=data,
        )]
        index_value = data.get("i") or data.get("indexPrice")
        funding = data.get("r") or data.get("fundingRate")
        if index_value is not None:
            result.append(RawMarketEvent(**common, event_type=EventType.INDEX_PRICE,
                                         exchange_ts_ms=timestamp, price=float(index_value), payload=data))
        if funding is not None:
            result.append(RawMarketEvent(**common, event_type=EventType.FUNDING,
                                         exchange_ts_ms=timestamp, quantity=float(funding), payload=data))
        return result
    return []


class PublicConnector:
    venue: Venue
    capabilities: dict[str, str]

    def __init__(self, symbols: tuple[str, ...]):
        self.symbols = tuple(_symbol(symbol) for symbol in symbols)
        self.resync_symbols: set[str] = set()
        self.connection_count = 0
        self.last_error: str | None = None

    async def request_resync(self, symbol: str) -> None:
        self.resync_symbols.add(_symbol(symbol))

    async def run(self, emit: Emit, stop: asyncio.Event) -> None:
        backoff = 1.0
        while not stop.is_set():
            try:
                await self._run_connection(emit, stop)
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}:{str(exc)[:180]}"
                logging.warning("forward_collector_reconnect venue=%s error=%s", self.venue.value, self.last_error)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, 30)

    async def _run_connection(self, emit: Emit, stop: asyncio.Event) -> None:
        raise NotImplementedError


class BinancePublicConnector(PublicConnector):
    venue = Venue.BINANCE
    capabilities = {
        "trades": "AGGRESSOR_LABELLED", "book": "INCREMENTAL_L2_WITH_REST_SNAPSHOT",
        "liquidations": "FORCE_ORDER_PUBLIC", "open_interest": "REST_POLLED",
        "funding_mark_index": "WEBSOCKET",
    }
    WS = "wss://fstream.binance.com/stream?streams="
    REST = "https://fapi.binance.com"

    async def _snapshot(self, session: aiohttp.ClientSession, symbol: str, emit: Emit, connection_id: str) -> None:
        received = now_ms()
        async with session.get(f"{self.REST}/fapi/v1/depth", params={"symbol": symbol, "limit": 1000}) as response:
            response.raise_for_status()
            data = await response.json(content_type=None)
        received = now_ms()
        await emit(RawMarketEvent(
            venue=self.venue, market="USD_M_PERPETUAL", symbol=symbol,
            instrument_type="PERPETUAL", event_type=EventType.BOOK_SNAPSHOT,
            exchange_ts_ms=received, receive_ts_ms=received,
            sequence_start=int(data["lastUpdateId"]), sequence_end=int(data["lastUpdateId"]),
            payload=data,
            connection_id=connection_id,
            metadata={"exchange_timestamp": "UNAVAILABLE_ON_REST_SNAPSHOT", "availability_time": "LOCAL_RECEIVE"},
        ))

    async def _poll_context(self, session: aiohttp.ClientSession, emit: Emit,
                            connection_id: str, stop: asyncio.Event) -> None:
        while not stop.is_set():
            for symbol in self.symbols:
                try:
                    async with session.get(f"{self.REST}/fapi/v1/openInterest", params={"symbol": symbol}) as response:
                        response.raise_for_status()
                        data = await response.json(content_type=None)
                    received = now_ms()
                    await emit(RawMarketEvent(
                        venue=self.venue, market="USD_M_PERPETUAL", symbol=symbol,
                        instrument_type="PERPETUAL", event_type=EventType.OPEN_INTEREST,
                        exchange_ts_ms=int(data.get("time") or received), receive_ts_ms=received,
                        quantity=float(data["openInterest"]), payload=data, connection_id=connection_id,
                        metadata={"unit": "CONTRACT_BASE_QUANTITY"},
                    ))
                except Exception as exc:
                    logging.warning("binance_oi_poll_failed symbol=%s error=%s", symbol, type(exc).__name__)
            try:
                await asyncio.wait_for(stop.wait(), timeout=30)
            except asyncio.TimeoutError:
                pass

    async def _run_connection(self, emit: Emit, stop: asyncio.Event) -> None:
        streams = []
        for symbol in self.symbols:
            token = symbol.lower()
            streams.extend((f"{token}@aggTrade", f"{token}@depth@100ms", f"{token}@forceOrder", f"{token}@markPrice@1s"))
        connection_id = f"BINANCE:{uuid.uuid4().hex}"
        timeout = aiohttp.ClientTimeout(total=None, connect=15, sock_read=90)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.ws_connect(self.WS + "/".join(streams), heartbeat=30, autoping=True) as ws:
                self.connection_count += 1
                for symbol in self.symbols:
                    await self._snapshot(session, symbol, emit, connection_id)
                poller = asyncio.create_task(self._poll_context(session, emit, connection_id, stop))
                try:
                    while not stop.is_set():
                        for symbol in tuple(self.resync_symbols):
                            await self._snapshot(session, symbol, emit, connection_id)
                            self.resync_symbols.discard(symbol)
                        message = await ws.receive(timeout=30)
                        if message.type == aiohttp.WSMsgType.TEXT:
                            for event in parse_binance_message(json.loads(message.data), receive_ts_ms=now_ms(), connection_id=connection_id):
                                await emit(event)
                        elif message.type in {aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR}:
                            raise ConnectionError(f"Binance websocket closed: {message.type}")
                finally:
                    poller.cancel()
                    await asyncio.gather(poller, return_exceptions=True)


class OKXPublicConnector(PublicConnector):
    venue = Venue.OKX
    capabilities = {
        "trades": "TAKER_SIDE", "book": "INCREMENTAL_L2_SEQ_CONTINUITY",
        "liquidations": "PUBLIC_LIQUIDATION_ORDERS", "open_interest": "WEBSOCKET",
        "funding_mark_index": "WEBSOCKET",
    }
    WS = "wss://ws.okx.com:8443/ws/v5/public"

    async def _run_connection(self, emit: Emit, stop: asyncio.Event) -> None:
        connection_id = f"OKX:{uuid.uuid4().hex}"
        args = []
        for symbol in self.symbols:
            inst_id = f"{symbol[:-4]}-USDT-SWAP"
            args.extend({"channel": channel, "instId": inst_id} for channel in (
                "trades", "books", "mark-price", "open-interest", "funding-rate",
            ))
            args.append({"channel": "index-tickers", "instId": f"{symbol[:-4]}-USDT"})
        args.append({"channel": "liquidation-orders", "instType": "SWAP"})
        timeout = aiohttp.ClientTimeout(total=None, connect=15, sock_read=None)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.ws_connect(self.WS, heartbeat=25, autoping=True) as ws:
                self.connection_count += 1
                await ws.send_json({"id": uuid.uuid4().hex[:16], "op": "subscribe", "args": args})
                while not stop.is_set():
                    if self.resync_symbols:
                        self.resync_symbols.clear()
                        raise ConnectionError("OKX_BOOK_RESYNC_REQUESTED")
                    try:
                        message = await ws.receive(timeout=20)
                    except asyncio.TimeoutError:
                        await ws.send_str("ping")
                        continue
                    if message.type == aiohttp.WSMsgType.TEXT:
                        if message.data == "pong":
                            continue
                        payload = json.loads(message.data)
                        if payload.get("event") == "error":
                            raise RuntimeError(f"OKX subscription error {payload.get('code')}:{payload.get('msg')}")
                        for event in parse_okx_message(payload, receive_ts_ms=now_ms(), connection_id=connection_id):
                            # liquidation-orders is subscribed by instrument type and can
                            # publish every SWAP; keep the preregistered universe only.
                            if event.symbol in self.symbols:
                                await emit(event)
                    elif message.type in {aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR}:
                        raise ConnectionError(f"OKX websocket closed: {message.type}")


class BingXPublicConnector(PublicConnector):
    venue = Venue.BINGX
    capabilities = {
        "trades": "AGGRESSOR_LABELLED", "book": "TOP20_SNAPSHOT_ONLY",
        "liquidations": "UNAVAILABLE", "open_interest": "REST_POLLED",
        "funding_mark_index": "WEBSOCKET_AND_REST",
    }
    WS = "wss://open-api-swap.bingx.com/swap-market"
    REST = "https://open-api.bingx.com"

    async def _poll_context(self, session: aiohttp.ClientSession, emit: Emit,
                            connection_id: str, stop: asyncio.Event) -> None:
        while not stop.is_set():
            for symbol in self.symbols:
                venue_symbol = f"{symbol[:-4]}-USDT"
                try:
                    async with session.get(f"{self.REST}/openApi/swap/v2/quote/openInterest",
                                           params={"symbol": venue_symbol}) as response:
                        response.raise_for_status()
                        oi_payload = await response.json(content_type=None)
                    received = now_ms()
                    oi_data = oi_payload.get("data") or {}
                    await emit(RawMarketEvent(
                        venue=self.venue, market="USDT_M_PERPETUAL", symbol=symbol,
                        instrument_type="PERPETUAL", event_type=EventType.OPEN_INTEREST,
                        exchange_ts_ms=int(oi_data.get("time") or received), receive_ts_ms=received,
                        quantity=float(oi_data.get("openInterest") or oi_data.get("openInterestValue") or 0),
                        payload=oi_payload, connection_id=connection_id,
                        metadata={"unit": "SOURCE_NATIVE"},
                    ))
                    async with session.get(f"{self.REST}/openApi/swap/v2/quote/premiumIndex",
                                           params={"symbol": venue_symbol}) as response:
                        response.raise_for_status()
                        premium_payload = await response.json(content_type=None)
                    premium = premium_payload.get("data") or {}
                    if isinstance(premium, list):
                        premium = premium[0] if premium else {}
                    received = now_ms()
                    timestamp = int(premium.get("time") or received)
                    for event_type, field, target in (
                        (EventType.MARK_PRICE, "markPrice", "price"),
                        (EventType.INDEX_PRICE, "indexPrice", "price"),
                        (EventType.FUNDING, "lastFundingRate", "quantity"),
                    ):
                        if premium.get(field) is not None:
                            kwargs = {target: float(premium[field])}
                            await emit(RawMarketEvent(
                                venue=self.venue, market="USDT_M_PERPETUAL", symbol=symbol,
                                instrument_type="PERPETUAL", event_type=event_type,
                                exchange_ts_ms=timestamp, receive_ts_ms=received,
                                payload=premium_payload, connection_id=connection_id, **kwargs,
                            ))
                except Exception as exc:
                    logging.warning("bingx_context_poll_failed symbol=%s error=%s", symbol, type(exc).__name__)
            try:
                await asyncio.wait_for(stop.wait(), timeout=30)
            except asyncio.TimeoutError:
                pass

    async def _run_connection(self, emit: Emit, stop: asyncio.Event) -> None:
        connection_id = f"BINGX:{uuid.uuid4().hex}"
        channels = []
        for symbol in self.symbols:
            venue_symbol = f"{symbol[:-4]}-USDT"
            channels.extend((f"{venue_symbol}@trade", f"{venue_symbol}@depth20@500ms", f"{venue_symbol}@markPrice"))
        timeout = aiohttp.ClientTimeout(total=None, connect=15, sock_read=None)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.ws_connect(self.WS, heartbeat=30, autoping=True) as ws:
                self.connection_count += 1
                for channel in channels:
                    await ws.send_json({"id": uuid.uuid4().hex, "reqType": "sub", "dataType": channel})
                poller = asyncio.create_task(self._poll_context(session, emit, connection_id, stop))
                try:
                    while not stop.is_set():
                        message = await ws.receive(timeout=30)
                        if message.type in {aiohttp.WSMsgType.BINARY, aiohttp.WSMsgType.TEXT}:
                            if message.type == aiohttp.WSMsgType.BINARY:
                                try:
                                    raw = gzip.decompress(message.data).decode("utf-8")
                                except (gzip.BadGzipFile, EOFError):
                                    raw = message.data.decode("utf-8")
                            else:
                                raw = message.data
                            if raw == "Ping":
                                await ws.send_str("Pong")
                                continue
                            payload = json.loads(raw)
                            for event in parse_bingx_message(payload, receive_ts_ms=now_ms(), connection_id=connection_id):
                                await emit(event)
                        elif message.type in {aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR}:
                            raise ConnectionError(f"BingX websocket closed: {message.type}")
                finally:
                    poller.cancel()
                    await asyncio.gather(poller, return_exceptions=True)


class ForwardCollectorSupervisor:
    def __init__(self, *, store: AppendOnlyEventStore, connectors: list[PublicConnector],
                 feature_interval_ms: int = 1_000,
                 snapshot_sink: Callable[[dict[str, Any], dict[str, Any]], None] | None = None):
        self.store = store
        self.connectors = {connector.venue.value: connector for connector in connectors}
        self.engine = MicrostructureFeatureEngine()
        self.cross = CrossVenueState()
        self.shadow = ForwardShadowEngine(store)
        self.labeler = ForwardOutcomeLabeler(store)
        self.feature_interval_ms = max(100, feature_interval_ms)
        self.snapshot_sink = snapshot_sink
        self.last_feature_ms: dict[tuple[str, str], int] = {}
        self.last_checkpoint_ms: dict[tuple[str, str], int] = {}
        self.last_exchange_ts: dict[tuple[str, str, str], int] = {}
        self.last_event_ms_by_venue: dict[str, int] = {}
        self.started_ms = now_ms()
        self.resync_inflight: set[tuple[str, str]] = set()
        self.events_received = 0
        self.duplicates = 0
        self.gaps = 0
        self.shadow_decisions = 0

    async def emit(self, event: RawMarketEvent) -> None:
        self.last_event_ms_by_venue[event.venue.value] = max(
            event.receive_ts_ms, self.last_event_ms_by_venue.get(event.venue.value, 0),
        )
        clock_key = event.venue.value, event.symbol, event.event_type.value
        previous = self.last_exchange_ts.get(clock_key)
        if previous is not None and event.exchange_ts_ms < previous:
            event = replace(event, integrity_status=IntegrityStatus.OUT_OF_ORDER)
        self.last_exchange_ts[clock_key] = max(previous or 0, event.exchange_ts_ms)
        receipt = self.store.append(event)
        self.events_received += 1
        if receipt["duplicate"]:
            self.duplicates += 1
            return
        result = self.engine.ingest(event, include_snapshot=False)
        if result["resync_required"]:
            self.gaps += 1
            key = event.venue.value, event.symbol
            if key not in self.resync_inflight:
                self.resync_inflight.add(key)
                connector = self.connectors[event.venue.value]
                await connector.request_resync(event.symbol)
                self.store.checkpoint(
                    recorded_ts_ms=event.receive_ts_ms, venue=event.venue.value,
                    symbol=event.symbol, connection_id=event.connection_id,
                    last_sequence=event.sequence_end, state="BOOK_INVALID_RESYNC_REQUESTED",
                    details={"book_result": result["book_result"]},
                )
            return
        if event.event_type is EventType.BOOK_SNAPSHOT:
            key = event.venue.value, event.symbol
            was_resync = key in self.resync_inflight
            self.resync_inflight.discard(key)
            if was_resync or event.receive_ts_ms - self.last_checkpoint_ms.get(key, 0) >= 30_000:
                self.store.checkpoint(
                    recorded_ts_ms=event.receive_ts_ms, venue=event.venue.value,
                    symbol=event.symbol, connection_id=event.connection_id,
                    last_sequence=event.sequence_end, state="BOOK_VALID",
                    details={"sequence_mode": self.engine.sequence_mode(event.venue.value)},
                )
                self.last_checkpoint_ms[key] = event.receive_ts_ms
        key = event.venue.value, event.symbol
        if event.receive_ts_ms - self.last_feature_ms.get(key, 0) < self.feature_interval_ms:
            return
        self.last_feature_ms[key] = event.receive_ts_ms
        snapshot = self.engine.snapshot(event.venue.value, event.symbol, event.receive_ts_ms)
        self.cross.update(snapshot)
        cross = self.cross.features(event.symbol, event.receive_ts_ms)
        self.store.append_feature(snapshot | {"cross_venue": cross})
        if self.snapshot_sink is not None:
            try:
                self.snapshot_sink(snapshot, cross)
            except Exception:
                # Shared current-state publication must not destroy the raw
                # append-only evidence stream during a transient DB outage.
                logging.exception("Forward current-state publication failed")
        decisions = self.shadow.evaluate(snapshot, cross)
        self.shadow_decisions += len(decisions)
        mid = snapshot.get("book", {}).get("mid")
        if mid:
            for decision in decisions:
                self.labeler.register(decision, float(mid))
            self.labeler.observe(
                venue=event.venue.value, symbol=event.symbol,
                observed_ts_ms=event.receive_ts_ms, mid=float(mid), book=snapshot["book"],
            )

    async def run(self, *, duration_seconds: float = 0) -> dict[str, Any]:
        stop = asyncio.Event()
        tasks = [asyncio.create_task(connector.run(self.emit, stop), name=f"collector-{name}")
                 for name, connector in self.connectors.items()]
        try:
            if duration_seconds > 0:
                await asyncio.sleep(duration_seconds)
            else:
                await asyncio.gather(*tasks)
        finally:
            stop.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        return self.health()

    def health(self) -> dict[str, Any]:
        current_ms = now_ms()
        warmup_ms = max(10_000, int(os.getenv("FORWARD_VENUE_WARMUP_SECONDS", "120")) * 1000)
        stale_ms = max(5_000, int(os.getenv("FORWARD_VENUE_STALE_SECONDS", "60")) * 1000)

        def venue_health(connector: PublicConnector) -> dict[str, Any]:
            last_event_ms = self.last_event_ms_by_venue.get(connector.venue.value)
            age_ms = max(0, current_ms - last_event_ms) if last_event_ms else None
            uptime_ms = max(0, current_ms - self.started_ms)
            if connector.last_error:
                state = "DEGRADED" if connector.connection_count > 0 or last_event_ms else "DISCONNECTED"
            elif last_event_ms and age_ms is not None and age_ms <= stale_ms:
                state = "HEALTHY"
            elif last_event_ms:
                state = "STALE"
            elif connector.connection_count > 0 and uptime_ms <= warmup_ms:
                state = "CONNECTED"
            elif uptime_ms <= warmup_ms:
                state = "WARMING"
            else:
                state = "DISCONNECTED"
            return {
                "state": state,
                "connection_count": connector.connection_count,
                "last_event_at_ms": last_event_ms,
                "event_age_ms": age_ms,
                "last_error": connector.last_error,
                "capabilities": connector.capabilities,
            }

        return {
            "events_received": self.events_received, "duplicates": self.duplicates,
            "book_gaps": self.gaps, "shadow_decisions": self.shadow_decisions,
            "store_counts": self.store.counts(),
            "venues": {
                name: venue_health(connector)
                for name, connector in sorted(self.connectors.items())
            },
            "execution_authority": False,
        }
