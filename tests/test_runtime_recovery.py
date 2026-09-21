from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import database.database as database
from services.forward_event_store import AppendOnlyEventStore, Venue
from services.forward_public_collectors import (
    BinancePublicConnector, BingXPublicConnector, ForwardCollectorSupervisor,
    OKXPublicConnector, PublicConnector,
)
from services.pump_dump_monitor import PumpDumpMonitor
from services.pump_dump_scanner import Candle
from services.runtime_supervision import PeriodicHeartbeatThread, RestartingTaskSupervisor


def _sqlite(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(database, "USE_POSTGRES", False)
    monkeypatch.setattr(database, "REQUIRE_PERSISTENT_DB", False)
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "DATABASE_NAME", tmp_path / "runtime-recovery.sqlite3")
    database.create_tables()


def test_current_public_provider_endpoints_are_pinned() -> None:
    assert BinancePublicConnector.WS == "wss://fstream.binance.com/public/stream?streams="
    assert BingXPublicConnector.WS == "wss://open-api-swap.bingx.com/swap-market"
    assert OKXPublicConnector.WS == "wss://ws.okx.com:8443/ws/v5/public"


def test_heartbeat_thread_survives_event_loop_stall_and_transient_publication_failure() -> None:
    attempts = {"count": 0, "success": 0}

    def publish() -> None:
        attempts["count"] += 1
        if attempts["count"] <= 2:
            raise ConnectionError("injected PostgreSQL outage")
        attempts["success"] += 1

    heartbeat = PeriodicHeartbeatThread(publish, interval_seconds=.005)
    heartbeat.start()
    # A synchronous stall represents unrelated event-loop work; the heartbeat
    # owns a separate thread and must continue retrying through it.
    time.sleep(.06)
    heartbeat.stop()
    assert attempts["count"] >= 4
    assert attempts["success"] >= 2
    assert heartbeat.last_success_at is not None


class _ScannerFeed:
    def __init__(self, *, hang: bool = False) -> None:
        self.hang = hang
        self.observed_at = datetime.now(timezone.utc).replace(microsecond=0)

    async def instruments(self):
        if self.hang:
            await asyncio.Event().wait()
        return [{
            "symbol": f"R{index}USDT", "status": "TRADING", "contract_type": "PERPETUAL",
            "quote_volume": 100_000_000 - index, "change_24h_pct": 8 if index == 0 else 0,
        } for index in range(5)]

    async def candles(self, symbol: str):
        start = self.observed_at - timedelta(minutes=240)
        rows = []
        for index in range(241):
            price = 100 + index * .001
            if symbol == "R0USDT" and index >= 236:
                price *= 1 + .08 * ((index - 235) / 5)
            rows.append(Candle(
                opened_at=start + timedelta(minutes=index), open=price,
                high=price * 1.001, low=price * .999, close=price,
                quote_volume=5_000_000 if index == 240 else 1_000_000,
                trade_count=1_000 + index,
            ))
        return rows

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_scanner_continues_without_forward_enrichment_and_invents_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _sqlite(monkeypatch, tmp_path)
    monitor = PumpDumpMonitor(feed=_ScannerFeed())
    monitor.universe_limit = 5

    def unavailable(*_args, **_kwargs):
        raise ConnectionError("forward state unavailable")

    monkeypatch.setattr(monitor.forward, "latest_states", unavailable)
    result = await monitor.check_once()

    assert result["status"] == "ok"
    assert result["successfully_fetched"] == result["baseline_ready_symbols"] == 5
    assert result["shortlisted_symbols"] >= 1
    assert result["deep_enrichment_symbols"] == 0
    assert result["enrichment_status"] == "DEGRADED"
    assert result["forward_microstructure_state"] == "UNAVAILABLE"
    with database.connect() as connection:
        payloads = [json.loads(row[0]) for row in connection.execute(
            "SELECT snapshot_json FROM market_anomaly_events"
        ).fetchall()]
    assert payloads
    assert all(item.get("cvd") is None and item.get("book_imbalance") is None for item in payloads)


@pytest.mark.asyncio
async def test_scanner_cycle_hard_timeout_records_stage_and_future_cycle_can_recover(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _sqlite(monkeypatch, tmp_path)
    feed = _ScannerFeed(hang=True)
    monitor = PumpDumpMonitor(feed=feed)
    monitor.cycle_timeout_seconds = .05
    monitor.interval_seconds = .01
    runner = asyncio.create_task(monitor.run_forever())
    await asyncio.sleep(.12)
    feed.hang = False
    monitor.cycle_timeout_seconds = 2
    await asyncio.sleep(.5)
    monitor.stop()
    await asyncio.wait_for(runner, timeout=1)
    with database.connect() as connection:
        runtime = connection.execute(
            "SELECT last_success_at,last_error,details_json FROM runtime_state WHERE worker_name=?",
            (monitor.worker_name,),
        ).fetchone()
    assert runtime is not None
    assert runtime["last_success_at"] is not None
    # A successful later cycle clears the transient timeout while proving the loop advanced.
    assert runtime["last_error"] is None


@pytest.mark.asyncio
async def test_component_failure_is_restarted_while_independent_heartbeat_advances() -> None:
    shutdown = asyncio.Event()
    attempts = {"worker": 0, "heartbeat": 0}

    class Worker:
        async def run_forever(self):
            attempts["worker"] += 1
            if attempts["worker"] == 1:
                raise RuntimeError("injected scanner failure")
            await shutdown.wait()

        def stop(self):
            return None

    supervisor = RestartingTaskSupervisor(
        "scanner", Worker, shutdown=shutdown,
        base_backoff_seconds=.01, max_backoff_seconds=.02,
    )

    async def heartbeat():
        while not shutdown.is_set():
            attempts["heartbeat"] += 1
            await asyncio.sleep(.005)

    tasks = [asyncio.create_task(supervisor.run()), asyncio.create_task(heartbeat())]
    await asyncio.sleep(.08)
    shutdown.set()
    await asyncio.gather(*tasks)
    assert attempts["worker"] >= 2
    assert supervisor.restart_count >= 1
    assert attempts["heartbeat"] >= 5


@pytest.mark.asyncio
async def test_temporary_database_operation_failure_recovers_on_next_scanner_cycle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _sqlite(monkeypatch, tmp_path)
    monitor = PumpDumpMonitor(feed=_ScannerFeed())
    monitor.universe_limit = 5
    original = monitor.repository.subscribers
    attempts = {"count": 0}

    def flaky_subscribers():
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise ConnectionError("injected PostgreSQL outage")
        return original()

    monkeypatch.setattr(monitor.repository, "subscribers", flaky_subscribers)
    with pytest.raises(ConnectionError, match="PostgreSQL outage"):
        await monitor.check_once()
    recovered = await monitor.check_once()
    assert recovered["status"] == "ok"
    assert recovered["successfully_fetched"] == 5
    with database.connect() as connection:
        state = connection.execute(
            "SELECT last_success_at,last_error FROM runtime_state WHERE worker_name=?",
            (monitor.worker_name,),
        ).fetchone()
    assert state["last_success_at"] is not None and state["last_error"] is None


@pytest.mark.asyncio
async def test_dead_provider_task_restarts_without_stopping_other_provider(tmp_path: Path) -> None:
    stop_seen = {"healthy_ticks": 0, "dead_attempts": 0}

    class Connector:
        capabilities = {}
        connection_count = 0
        reconnect_count = 0
        connected = False
        last_error = None
        last_error_at_ms = None
        last_connected_at_ms = None
        symbols = ("BTCUSDT",)
        base_backoff_seconds = .01

        def __init__(self, venue: Venue, dead: bool = False):
            self.venue = venue
            self.dead = dead

        async def run(self, _emit, stop):
            if self.dead:
                stop_seen["dead_attempts"] += 1
                if stop_seen["dead_attempts"] == 1:
                    raise RuntimeError("injected provider task death")
            while not stop.is_set():
                stop_seen["healthy_ticks"] += int(not self.dead)
                await asyncio.sleep(.005)

        async def request_resync(self, _symbol):
            return None

    supervisor = ForwardCollectorSupervisor(
        store=AppendOnlyEventStore(tmp_path / "provider-isolation.sqlite3"),
        connectors=[Connector(Venue.BINANCE, dead=True), Connector(Venue.OKX)],
    )
    await supervisor.run(duration_seconds=1.15)
    assert stop_seen["dead_attempts"] >= 2
    assert stop_seen["healthy_ticks"] >= 20


@pytest.mark.asyncio
async def test_hung_provider_connection_is_cancelled_and_recreated(tmp_path: Path) -> None:
    class HungConnector(PublicConnector):
        venue = Venue.BINANCE
        capabilities = {}

        def __init__(self):
            super().__init__(("BTCUSDT",))
            self.connection_max_seconds = .03
            self.base_backoff_seconds = .01
            self.starts = 0
            self.cancellations = 0

        async def _run_connection(self, _emit, _stop):
            self.starts += 1
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancellations += 1
                raise

    connector = HungConnector()
    supervisor = ForwardCollectorSupervisor(
        store=AppendOnlyEventStore(tmp_path / "hung-provider.sqlite3"),
        connectors=[connector],
    )
    await supervisor.run(duration_seconds=.14)
    assert connector.starts >= 2
    assert connector.cancellations >= 2
    assert connector.reconnect_count >= 1
    assert connector.last_restart_reason and "TimeoutError" in connector.last_restart_reason


def test_repeated_scanner_cycle_does_not_duplicate_economic_event(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _sqlite(monkeypatch, tmp_path)
    monitor = PumpDumpMonitor(feed=_ScannerFeed())
    monitor.universe_limit = 5
    asyncio.run(monitor.check_once())
    with database.connect() as connection:
        before = connection.execute("SELECT COUNT(*) FROM market_anomaly_events").fetchone()[0]
    asyncio.run(monitor.check_once())
    with database.connect() as connection:
        after = connection.execute("SELECT COUNT(*) FROM market_anomaly_events").fetchone()[0]
    assert before > 0
    assert after == before
