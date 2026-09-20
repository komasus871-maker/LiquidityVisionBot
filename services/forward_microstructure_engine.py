"""Causal event-driven books, flow aggregation, and feature snapshots."""
from __future__ import annotations

import math
import statistics
import zlib
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Iterable

from services.forward_event_store import EventType, IntegrityStatus, RawMarketEvent


FEATURE_SCHEMA_VERSION = "forward-microstructure-feature-v1"
HORIZONS_MS = (1_000, 5_000, 15_000, 30_000, 60_000, 300_000)


def _levels(values: Iterable[Iterable[Any]]) -> list[tuple[float, float]]:
    result = []
    for row in values:
        pair = list(row)
        if len(pair) < 2:
            raise ValueError("book level requires price and quantity")
        price, quantity = float(pair[0]), float(pair[1])
        if price <= 0 or quantity < 0 or not math.isfinite(price) or not math.isfinite(quantity):
            raise ValueError("invalid book level")
        result.append((price, quantity))
    return result


def _level_records(values: Iterable[Iterable[Any]]) -> list[tuple[float, float, str, str]]:
    result = []
    for row in values:
        pair = list(row)
        if len(pair) < 2:
            raise ValueError("book level requires price and quantity")
        price_text, quantity_text = str(pair[0]), str(pair[1])
        price, quantity = float(price_text), float(quantity_text)
        if price <= 0 or quantity < 0 or not math.isfinite(price) or not math.isfinite(quantity):
            raise ValueError("invalid book level")
        result.append((price, quantity, price_text, quantity_text))
    return result


def okx_checksum(bids: dict[float, float], asks: dict[float, float],
                 bid_text: dict[float, tuple[str, str]] | None = None,
                 ask_text: dict[float, tuple[str, str]] | None = None) -> int:
    bid_levels = sorted(bids.items(), reverse=True)[:25]
    ask_levels = sorted(asks.items())[:25]
    parts: list[str] = []
    for index in range(max(len(bid_levels), len(ask_levels))):
        if index < len(bid_levels):
            price, quantity = bid_levels[index]
            parts.extend((bid_text[price] if bid_text and price in bid_text else (format(price, "g"), format(quantity, "g"))))
        if index < len(ask_levels):
            price, quantity = ask_levels[index]
            parts.extend((ask_text[price] if ask_text and price in ask_text else (format(price, "g"), format(quantity, "g"))))
    checksum = zlib.crc32(":".join(parts).encode("utf-8"))
    return checksum if checksum < 2**31 else checksum - 2**32


@dataclass
class BookApplyResult:
    status: IntegrityStatus
    code: str
    applied: bool
    resync_required: bool = False


class L2Book:
    def __init__(self, venue: str, symbol: str, *, sequence_mode: str):
        if sequence_mode not in {"BINANCE", "OKX", "SNAPSHOT_ONLY"}:
            raise ValueError("unknown sequence mode")
        self.venue = venue
        self.symbol = symbol
        self.sequence_mode = sequence_mode
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}
        self.bid_text: dict[float, tuple[str, str]] = {}
        self.ask_text: dict[float, tuple[str, str]] = {}
        self.last_sequence: int | None = None
        self.snapshot_pending = False
        self.last_exchange_ts_ms: int | None = None
        self.last_receive_ts_ms: int | None = None
        self.status = IntegrityStatus.UNKNOWN
        self.gap_count = 0
        self.snapshot_count = 0
        self.delta_count = 0

    @staticmethod
    def _apply_levels(side: dict[float, float], changes: list[tuple[float, float]]) -> None:
        for price, quantity in changes:
            if quantity == 0:
                side.pop(price, None)
            else:
                side[price] = quantity

    def _structurally_valid(self) -> bool:
        return bool(self.bids and self.asks and max(self.bids) < min(self.asks))

    def apply_snapshot(self, bids: Iterable[Iterable[Any]], asks: Iterable[Iterable[Any]], *,
                       sequence: int | None, exchange_ts_ms: int, receive_ts_ms: int,
                       checksum: int | None = None) -> BookApplyResult:
        bid_records, ask_records = _level_records(bids), _level_records(asks)
        next_bids = {price: quantity for price, quantity, _, _ in bid_records if quantity > 0}
        next_asks = {price: quantity for price, quantity, _, _ in ask_records if quantity > 0}
        next_bid_text = {price: (price_text, quantity_text) for price, quantity, price_text, quantity_text in bid_records if quantity > 0}
        next_ask_text = {price: (price_text, quantity_text) for price, quantity, price_text, quantity_text in ask_records if quantity > 0}
        old = self.bids, self.asks, self.bid_text, self.ask_text
        self.bids, self.asks = next_bids, next_asks
        self.bid_text, self.ask_text = next_bid_text, next_ask_text
        if not self._structurally_valid():
            self.bids, self.asks, self.bid_text, self.ask_text = old
            self.status = IntegrityStatus.INVALID
            return BookApplyResult(self.status, "CROSSED_OR_EMPTY_SNAPSHOT", False, True)
        if checksum is not None and self.sequence_mode == "OKX" and okx_checksum(self.bids, self.asks, self.bid_text, self.ask_text) != checksum:
            self.bids, self.asks, self.bid_text, self.ask_text = old
            self.status = IntegrityStatus.INVALID
            return BookApplyResult(self.status, "CHECKSUM_MISMATCH", False, True)
        self.last_sequence = sequence
        self.snapshot_pending = self.sequence_mode == "BINANCE"
        self.last_exchange_ts_ms = exchange_ts_ms
        self.last_receive_ts_ms = receive_ts_ms
        self.status = IntegrityStatus.VALID
        self.snapshot_count += 1
        return BookApplyResult(self.status, "SNAPSHOT_APPLIED", True)

    def apply_delta(self, bids: Iterable[Iterable[Any]], asks: Iterable[Iterable[Any]], *,
                    sequence_start: int | None, sequence_end: int | None,
                    previous_sequence: int | None, exchange_ts_ms: int,
                    receive_ts_ms: int, checksum: int | None = None) -> BookApplyResult:
        if self.sequence_mode == "SNAPSHOT_ONLY":
            self.status = IntegrityStatus.INVALID
            return BookApplyResult(self.status, "DELTA_UNSUPPORTED", False, True)
        if self.status is not IntegrityStatus.VALID or self.last_sequence is None or sequence_end is None:
            return BookApplyResult(self.status, "SNAPSHOT_REQUIRED", False, True)
        if sequence_end <= self.last_sequence:
            return BookApplyResult(IntegrityStatus.DUPLICATE, "OLD_OR_DUPLICATE_DELTA", False)
        expected = self.last_sequence + 1
        if self.sequence_mode == "BINANCE":
            if self.snapshot_pending:
                continuous = sequence_start is not None and sequence_start <= expected <= sequence_end
            else:
                continuous = (
                    previous_sequence == self.last_sequence
                    if previous_sequence is not None
                    else sequence_start is not None and sequence_start <= expected <= sequence_end
                )
        else:
            continuous = previous_sequence == self.last_sequence
        if not continuous:
            self.status = IntegrityStatus.GAPPED
            self.gap_count += 1
            return BookApplyResult(self.status, "SEQUENCE_GAP", False, True)
        bid_records, ask_records = _level_records(bids), _level_records(asks)
        next_bids, next_asks = dict(self.bids), dict(self.asks)
        next_bid_text, next_ask_text = dict(self.bid_text), dict(self.ask_text)
        self._apply_levels(next_bids, [(p, q) for p, q, _, _ in bid_records])
        self._apply_levels(next_asks, [(p, q) for p, q, _, _ in ask_records])
        for price, quantity, price_text, quantity_text in bid_records:
            if quantity == 0:
                next_bid_text.pop(price, None)
            else:
                next_bid_text[price] = (price_text, quantity_text)
        for price, quantity, price_text, quantity_text in ask_records:
            if quantity == 0:
                next_ask_text.pop(price, None)
            else:
                next_ask_text[price] = (price_text, quantity_text)
        old = self.bids, self.asks, self.bid_text, self.ask_text
        self.bids, self.asks = next_bids, next_asks
        self.bid_text, self.ask_text = next_bid_text, next_ask_text
        if not self._structurally_valid():
            self.bids, self.asks, self.bid_text, self.ask_text = old
            self.status = IntegrityStatus.INVALID
            return BookApplyResult(self.status, "CROSSED_OR_EMPTY_DELTA", False, True)
        if checksum is not None and self.sequence_mode == "OKX" and okx_checksum(self.bids, self.asks, self.bid_text, self.ask_text) != checksum:
            self.bids, self.asks, self.bid_text, self.ask_text = old
            self.status = IntegrityStatus.INVALID
            return BookApplyResult(self.status, "CHECKSUM_MISMATCH", False, True)
        self.last_sequence = sequence_end
        self.snapshot_pending = False
        self.last_exchange_ts_ms = exchange_ts_ms
        self.last_receive_ts_ms = receive_ts_ms
        self.delta_count += 1
        return BookApplyResult(self.status, "DELTA_APPLIED", True)

    def features(self, *, levels: int = 10, within_bps: float = 10.0) -> dict[str, Any]:
        if self.status is not IntegrityStatus.VALID or not self._structurally_valid():
            return {"status": self.status.value, "valid": False}
        bids = sorted(self.bids.items(), reverse=True)
        asks = sorted(self.asks.items())
        best_bid, bid_qty = bids[0]
        best_ask, ask_qty = asks[0]
        mid = (best_bid + best_ask) / 2
        spread = best_ask - best_bid
        bid_depth = sum(price * quantity for price, quantity in bids[:levels])
        ask_depth = sum(price * quantity for price, quantity in asks[:levels])
        depth_total = bid_depth + ask_depth
        top_total = bid_qty + ask_qty
        microprice = (best_ask * bid_qty + best_bid * ask_qty) / top_total if top_total else mid
        bid_cutoff, ask_cutoff = mid * (1 - within_bps / 10_000), mid * (1 + within_bps / 10_000)
        return {
            "status": self.status.value, "valid": True,
            "best_bid": best_bid, "best_ask": best_ask, "mid": mid,
            "spread": spread, "spread_bps": spread / mid * 10_000,
            "best_bid_quantity": bid_qty, "best_ask_quantity": ask_qty,
            "bid_depth_notional_top_n": bid_depth, "ask_depth_notional_top_n": ask_depth,
            "depth_imbalance": (bid_depth - ask_depth) / depth_total if depth_total else None,
            "top_level_imbalance": (bid_qty - ask_qty) / top_total if top_total else None,
            "microprice": microprice, "microprice_deviation_bps": (microprice / mid - 1) * 10_000,
            "bid_liquidity_within_bps": sum(p * q for p, q in bids if p >= bid_cutoff),
            "ask_liquidity_within_bps": sum(p * q for p, q in asks if p <= ask_cutoff),
            "sequence": self.last_sequence, "sequence_mode": self.sequence_mode,
            "exchange_ts_ms": self.last_exchange_ts_ms, "receive_ts_ms": self.last_receive_ts_ms,
            "gap_count": self.gap_count,
        }


@dataclass(frozen=True)
class TradePoint:
    exchange_ts_ms: int
    receive_ts_ms: int
    side: str
    price: float
    quantity: float

    @property
    def notional(self) -> float:
        return self.price * self.quantity


@dataclass(frozen=True)
class LiquidationPoint:
    exchange_ts_ms: int
    receive_ts_ms: int
    side: str
    notional: float


class MicrostructureFeatureEngine:
    """Maintains present state only; it has no order or execution dependency."""

    def __init__(self, *, stale_after_ms: int = 5_000):
        self.stale_after_ms = stale_after_ms
        self.books: dict[tuple[str, str], L2Book] = {}
        self.trades: dict[tuple[str, str], deque[TradePoint]] = defaultdict(deque)
        self.liquidations: dict[tuple[str, str], deque[LiquidationPoint]] = defaultdict(deque)
        self.cvd: dict[tuple[str, str], float] = defaultdict(float)
        self.derivatives: dict[tuple[str, str], dict[str, Any]] = defaultdict(dict)
        self.latest_trade_price: dict[tuple[str, str], float] = {}
        self.last_event_receive: dict[tuple[str, str], int] = {}
        self.out_of_order: dict[tuple[str, str], int] = defaultdict(int)

    @staticmethod
    def sequence_mode(venue: str) -> str:
        return {"BINANCE": "BINANCE", "OKX": "OKX", "BINGX": "SNAPSHOT_ONLY"}[venue]

    def ingest(self, event: RawMarketEvent, *, include_snapshot: bool = True) -> dict[str, Any]:
        key = event.venue.value, event.symbol
        previous_receive = self.last_event_receive.get(key)
        if previous_receive is not None and event.receive_ts_ms < previous_receive:
            self.out_of_order[key] += 1
        self.last_event_receive[key] = max(previous_receive or 0, event.receive_ts_ms)
        resync_required = False
        book_result: BookApplyResult | None = None
        if event.event_type in {EventType.BOOK_SNAPSHOT, EventType.BOOK_DELTA}:
            book = self.books.setdefault(key, L2Book(*key, sequence_mode=self.sequence_mode(event.venue.value)))
            payload = event.payload
            bids = payload.get("bids", payload.get("b", []))
            asks = payload.get("asks", payload.get("a", []))
            checksum = payload.get("checksum")
            if event.venue.value == "OKX" and checksum in {0, "0"}:
                checksum = None
            if event.event_type is EventType.BOOK_SNAPSHOT:
                book_result = book.apply_snapshot(
                    bids, asks, sequence=event.sequence_end,
                    exchange_ts_ms=event.exchange_ts_ms, receive_ts_ms=event.receive_ts_ms,
                    checksum=checksum,
                )
            else:
                book_result = book.apply_delta(
                    bids, asks,
                    sequence_start=event.sequence_start, sequence_end=event.sequence_end,
                    previous_sequence=event.previous_sequence,
                    exchange_ts_ms=event.exchange_ts_ms, receive_ts_ms=event.receive_ts_ms,
                    checksum=checksum,
                )
            resync_required = book_result.resync_required
        elif event.event_type is EventType.TRADE:
            if event.side not in {"BUY", "SELL"} or event.price is None or event.quantity is None:
                raise ValueError("trade requires aggressor side, price, and quantity")
            point = TradePoint(event.exchange_ts_ms, event.receive_ts_ms, event.side, event.price, event.quantity)
            self.trades[key].append(point)
            self.latest_trade_price[key] = event.price
            self.cvd[key] += point.notional if point.side == "BUY" else -point.notional
            self._prune(key, event.receive_ts_ms)
        elif event.event_type is EventType.LIQUIDATION:
            if event.side not in {"BUY", "SELL"} or event.price is None or event.quantity is None:
                raise ValueError("liquidation requires order side, price, and quantity")
            self.liquidations[key].append(LiquidationPoint(
                event.exchange_ts_ms, event.receive_ts_ms, event.side, event.price * event.quantity,
            ))
            self._prune(key, event.receive_ts_ms)
        elif event.event_type in {EventType.OPEN_INTEREST, EventType.FUNDING, EventType.MARK_PRICE, EventType.INDEX_PRICE}:
            field = {
                EventType.OPEN_INTEREST: "open_interest",
                EventType.FUNDING: "funding_rate",
                EventType.MARK_PRICE: "mark_price",
                EventType.INDEX_PRICE: "index_price",
            }[event.event_type]
            value = event.quantity if event.event_type in {EventType.OPEN_INTEREST, EventType.FUNDING} else event.price
            if event.event_type is EventType.OPEN_INTEREST:
                previous = self.derivatives[key].get("open_interest")
                self.derivatives[key]["open_interest_change"] = (
                    value / previous - 1 if previous not in {None, 0} and value is not None else None
                )
            self.derivatives[key][field] = value
            self.derivatives[key][f"{field}_exchange_ts_ms"] = event.exchange_ts_ms
            self.derivatives[key][f"{field}_receive_ts_ms"] = event.receive_ts_ms
        return {
            "book_result": None if book_result is None else book_result.__dict__,
            "resync_required": resync_required,
            "snapshot": self.snapshot(event.venue.value, event.symbol, event.receive_ts_ms)
            if include_snapshot else None,
        }

    def _prune(self, key: tuple[str, str], now_ms: int) -> None:
        cutoff = now_ms - HORIZONS_MS[-1]
        while self.trades[key] and self.trades[key][0].receive_ts_ms < cutoff:
            self.trades[key].popleft()
        while self.liquidations[key] and self.liquidations[key][0].receive_ts_ms < cutoff:
            self.liquidations[key].popleft()

    @staticmethod
    def _flow(points: deque[TradePoint], now_ms: int, horizon_ms: int) -> dict[str, Any]:
        values = [point for point in points if point.receive_ts_ms > now_ms - horizon_ms]
        buy = sum(point.notional for point in values if point.side == "BUY")
        sell = sum(point.notional for point in values if point.side == "SELL")
        total = buy + sell
        counts_buy = sum(point.side == "BUY" for point in values)
        counts_sell = len(values) - counts_buy
        notionals = [point.notional for point in values]
        threshold = statistics.quantiles(notionals, n=20)[-1] if len(notionals) >= 20 else None
        large_delta = None if threshold is None else sum(
            point.notional if point.side == "BUY" else -point.notional
            for point in values if point.notional >= threshold
        )
        return {
            "trade_count": len(values), "buy_count": counts_buy, "sell_count": counts_sell,
            "buy_notional": buy, "sell_notional": sell, "delta_notional": buy - sell,
            "normalized_delta": (buy - sell) / total if total else None,
            "average_trade_notional": total / len(values) if values else None,
            "large_trade_delta": large_delta,
            "flow_concentration": max(notionals) / total if total and notionals else None,
            "burst_per_second": len(values) / (horizon_ms / 1000),
        }

    @staticmethod
    def _liquidation(points: deque[LiquidationPoint], now_ms: int, horizon_ms: int) -> dict[str, Any]:
        values = [point for point in points if point.receive_ts_ms > now_ms - horizon_ms]
        forced_buys = sum(point.notional for point in values if point.side == "BUY")
        forced_sells = sum(point.notional for point in values if point.side == "SELL")
        total = forced_buys + forced_sells
        return {
            "count": len(values), "forced_buy_notional": forced_buys,
            "forced_sell_notional": forced_sells,
            "net_forced_order_notional": forced_buys - forced_sells,
            "imbalance": (forced_buys - forced_sells) / total if total else None,
        }

    def snapshot(self, venue: str, symbol: str, receive_ts_ms: int) -> dict[str, Any]:
        key = venue, symbol
        book = self.books.get(key)
        book_features = book.features() if book else {"status": IntegrityStatus.UNKNOWN.value, "valid": False}
        last_exchange = max(
            [point.exchange_ts_ms for point in self.trades[key]]
            + ([book.last_exchange_ts_ms] if book and book.last_exchange_ts_ms is not None else [])
            + [int(value) for name, value in self.derivatives[key].items() if name.endswith("_exchange_ts_ms")]
            + [0]
        )
        age_ms = max(0, receive_ts_ms - last_exchange) if last_exchange else None
        book_valid = bool(book_features.get("valid"))
        flow_valid = bool(self.trades[key])
        status = "VALID" if book_valid and flow_valid and age_ms is not None and age_ms <= self.stale_after_ms else (
            "STALE" if age_ms is not None and age_ms > self.stale_after_ms else "INSUFFICIENT"
        )
        derivatives = dict(self.derivatives[key])
        mark, index = derivatives.get("mark_price"), derivatives.get("index_price")
        derivatives["basis_bps"] = ((mark / index - 1) * 10_000) if mark and index else None
        flow = {str(horizon): self._flow(self.trades[key], receive_ts_ms, horizon) for horizon in HORIZONS_MS}
        liquidation = {str(horizon): self._liquidation(self.liquidations[key], receive_ts_ms, horizon)
                       for horizon in (60_000, 300_000)}
        one_minute = flow[str(60_000)]
        progress = None
        points = list(self.trades[key])
        if points and points[0].price:
            progress = (points[-1].price / points[0].price - 1) * 10_000
        state = "BALANCED"
        if book_valid and one_minute["normalized_delta"] is not None:
            pressure = float(book_features.get("depth_imbalance") or 0)
            delta = float(one_minute["normalized_delta"])
            if delta >= .20 and pressure >= .15:
                state = "BUY_PRESSURE"
            elif delta <= -.20 and pressure <= -.15:
                state = "SELL_PRESSURE"
            elif delta >= .30 and progress is not None and progress < 2:
                state = "BUY_ABSORPTION"
            elif delta <= -.30 and progress is not None and progress > -2:
                state = "SELL_ABSORPTION"
            elif book_features.get("ask_liquidity_within_bps", math.inf) < 50_000 and delta > .15:
                state = "LIQUIDITY_VACUUM_UP"
            elif book_features.get("bid_liquidity_within_bps", math.inf) < 50_000 and delta < -.15:
                state = "LIQUIDITY_VACUUM_DOWN"
            elif float(book_features.get("spread_bps") or 0) > 5:
                state = "HIGH_SPREAD"
        if liquidation[str(60_000)]["forced_sell_notional"] > 250_000:
            state = "LONG_LIQUIDATION"
        elif liquidation[str(60_000)]["forced_buy_notional"] > 250_000:
            state = "SHORT_LIQUIDATION"
        return {
            "schema_version": FEATURE_SCHEMA_VERSION,
            "timestamp_ms": last_exchange or receive_ts_ms,
            "receive_ts_ms": receive_ts_ms,
            "venue": venue, "symbol": symbol,
            "data_quality": {
                "status": status, "age_ms": age_ms,
                "book_status": book_features.get("status"),
                "out_of_order_count": self.out_of_order[key],
                "event_clock": "RECEIVE_TIME_CAUSAL",
            },
            "trade_flow": {"cvd_notional": self.cvd[key], "horizons_ms": flow},
            "book": book_features, "liquidations": liquidation,
            "derivatives": derivatives, "price_progress_5m_bps": progress,
            "market_state": state, "execution_authority": False,
        }


class CrossVenueState:
    def __init__(self, *, maximum_age_ms: int = 3_000):
        self.maximum_age_ms = maximum_age_ms
        self.snapshots: dict[tuple[str, str], dict[str, Any]] = {}
        self.mid_history: dict[tuple[str, str], deque[tuple[int, float]]] = defaultdict(deque)

    def update(self, snapshot: dict[str, Any]) -> None:
        key = snapshot["venue"], snapshot["symbol"]
        self.snapshots[key] = snapshot
        mid = snapshot.get("book", {}).get("mid")
        if mid:
            history = self.mid_history[key]
            history.append((int(snapshot["receive_ts_ms"]), float(mid)))
            cutoff = int(snapshot["receive_ts_ms"]) - 5_000
            while len(history) > 1 and history[0][0] < cutoff:
                history.popleft()

    def features(self, symbol: str, now_ms: int) -> dict[str, Any]:
        active = {
            venue: snapshot for (venue, current_symbol), snapshot in self.snapshots.items()
            if current_symbol == symbol
            and snapshot["data_quality"]["status"] == "VALID"
            and now_ms - snapshot["receive_ts_ms"] <= self.maximum_age_ms
            and snapshot["book"].get("mid")
        }
        if len(active) < 2:
            return {"status": "INSUFFICIENT", "venue_count": len(active)}
        mids = {venue: float(snapshot["book"]["mid"]) for venue, snapshot in active.items()}
        reference = statistics.median(mids.values())
        deviations = {venue: (mid / reference - 1) * 10_000 for venue, mid in mids.items()}
        moves = {}
        for venue in active:
            history = self.mid_history[(venue, symbol)]
            moves[venue] = (history[-1][1] / history[0][1] - 1) * 10_000 if len(history) >= 2 else 0.0
        deltas = {
            venue: snapshot["trade_flow"]["horizons_ms"]["5000"]["normalized_delta"]
            for venue, snapshot in active.items()
        }
        return {
            "status": "VALID", "venue_count": len(active), "reference_mid": reference,
            "mid_deviation_bps": deviations, "flow_5s": deltas,
            "mid_move_5s_bps": moves,
            "leading_move_venue": max(moves, key=lambda venue: abs(moves[venue])),
            "highest_mid_venue": max(mids, key=mids.get), "lowest_mid_venue": min(mids, key=mids.get),
            "dispersion_bps": (max(mids.values()) / min(mids.values()) - 1) * 10_000,
            "as_of_receive_ts_ms": now_ms,
        }
