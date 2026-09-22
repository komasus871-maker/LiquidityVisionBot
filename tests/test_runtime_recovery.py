from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import database.database as database
from services.forward_event_store import AppendOnlyEventStore, Venue
from services.forward_public_collectors import (
    BinancePublicConnector, BingXPublicConnector, ForwardCollectorSupervisor,
    OKXPublicConnector, PublicConnector,
)
from services.pump_dump_monitor import (
    BinanceFuturesBroadFeed, OKXFuturesBroadFeed, ProviderFailoverBroadFeed,
    PumpDumpMonitor,
)
from services.pump_dump_scanner import Candle
from services.runtime_supervision import (
    PeriodicHeartbeatThread, ProviderRegionBlockedError, RestartingTaskSupervisor,
    wait_for_authoritative_lease,
)
from tools.run_forward_microstructure_collector import (
    _SingleFlightThreadCall, _run_archive_maintenance_cycle,
)


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


@pytest.mark.asyncio
async def test_valid_authoritative_lease_waits_then_acquires_without_crashing() -> None:
    available = False
    attempts = 0

    def acquire(_name: str, _owner: str, _ttl: int) -> bool:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("injected transient lease database outage")
        return available

    shutdown = asyncio.Event()
    waiter = asyncio.create_task(wait_for_authoritative_lease(
        acquire, "forward-test", "new-owner", 60, shutdown=shutdown,
        base_backoff_seconds=.01, max_backoff_seconds=.01,
    ))
    await asyncio.sleep(.08)
    assert not waiter.done()
    available = True
    assert await asyncio.wait_for(waiter, timeout=.2)
    assert attempts >= 2


def test_expired_authoritative_lease_is_atomically_reclaimed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _sqlite(monkeypatch, tmp_path)
    expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO distributed_leases(lease_name,owner_id,expires_at,updated_at) VALUES(?,?,?,?)",
            ("forward-stale", "old-owner", expired, expired),
        )
    assert database.acquire_lease("forward-stale", "new-owner", 60)
    with database.connect() as connection:
        row = connection.execute(
            "SELECT owner_id FROM distributed_leases WHERE lease_name=?", ("forward-stale",),
        ).fetchone()
    assert row["owner_id"] == "new-owner"


@pytest.mark.asyncio
async def test_binance_451_is_classified_and_suppresses_repeat_rest_requests() -> None:
    class Response:
        status = 451

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class Session:
        closed = False

        def __init__(self):
            self.calls = 0

        def get(self, *_args, **_kwargs):
            self.calls += 1
            return Response()

        async def close(self):
            self.closed = True

    feed = BinanceFuturesBroadFeed()
    session = Session()
    feed._session = session
    with pytest.raises(ProviderRegionBlockedError, match="HTTP_451"):
        await feed.instruments()
    with pytest.raises(ProviderRegionBlockedError, match="HTTP_451"):
        await feed.instruments()
    assert session.calls == 1
    assert feed.region_blocked_until_at is not None


@pytest.mark.asyncio
async def test_region_blocked_provider_isolated_while_other_provider_runs(tmp_path: Path) -> None:
    ticks = {"blocked": 0, "healthy": 0}

    class Blocked(PublicConnector):
        venue = Venue.BINANCE
        capabilities = {}

        async def _run_connection(self, _emit, _stop):
            ticks["blocked"] += 1
            raise ProviderRegionBlockedError("BINANCE")

    class Healthy(PublicConnector):
        venue = Venue.OKX
        capabilities = {}

        async def _run_connection(self, _emit, stop):
            while not stop.is_set():
                ticks["healthy"] += 1
                await asyncio.sleep(.005)

    blocked = Blocked(("BTCUSDT",))
    blocked.region_block_backoff_seconds = 1
    supervisor = ForwardCollectorSupervisor(
        store=AppendOnlyEventStore(tmp_path / "region-isolation.sqlite3"),
        connectors=[blocked, Healthy(("BTCUSDT",))],
    )
    runner = asyncio.create_task(supervisor.run(duration_seconds=.08))
    await asyncio.sleep(.03)
    health = supervisor.health()
    assert health["venues"]["BINANCE"]["state"] == "REGION_BLOCKED"
    await runner
    assert ticks["blocked"] == 1
    assert ticks["healthy"] >= 5
    assert blocked.task_state in {"REGION_BLOCKED", "CANCELLED"}
    assert blocked.last_error == "PROVIDER_REGION_BLOCKED:BINANCE:HTTP_451"


@pytest.mark.asyncio
async def test_scanner_451_reports_degraded_and_loop_stays_alive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _sqlite(monkeypatch, tmp_path)

    class RegionBlockedFeed:
        region_blocked_until_at = "2099-01-01T00:00:00+00:00"

        def __init__(self):
            self.calls = 0

        async def instruments(self):
            self.calls += 1
            raise ProviderRegionBlockedError("BINANCE")

        async def close(self):
            return None

    feed = RegionBlockedFeed()
    monitor = PumpDumpMonitor(feed=feed)
    first = await monitor.check_once()
    assert first["status"] == "degraded"
    assert first["provider_state"] == "REGION_BLOCKED"
    assert first["successfully_fetched"] == 0
    monitor.interval_seconds = .01
    runner = asyncio.create_task(monitor.run_forever())
    await asyncio.sleep(.05)
    assert not runner.done()
    monitor.stop()
    await asyncio.wait_for(runner, timeout=1)
    with database.connect() as connection:
        state = connection.execute(
            "SELECT last_error,details_json FROM runtime_state WHERE worker_name=?",
            (monitor.worker_name,),
        ).fetchone()
    assert "PROVIDER_REGION_BLOCKED:BINANCE:HTTP_451" in state["last_error"]
    assert json.loads(state["details_json"])["provider_state"] == "REGION_BLOCKED"


@pytest.mark.asyncio
async def test_archive_timeout_degrades_cycle_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FORWARD_ARCHIVE_OPERATION_TIMEOUT_SECONDS", ".01")

    class Ledger:
        def seal_completed(self):
            return None

    class Store:
        raw_ledger = Ledger()

    class Archive:
        calls = 0

        def process_pending(self):
            self.calls += 1
            time.sleep(.04)

        def evict_verified(self, **_kwargs):
            raise AssertionError("later archive stages must not run after timeout")

    runtime = {
        "task_state": "STARTING", "restart_count": 0,
        "last_error": None, "last_success_at": None,
    }
    archive = Archive()
    calls = _SingleFlightThreadCall()
    _, _, succeeded = await _run_archive_maintenance_cycle(
        archive, Store(), runtime, last_compaction=0, last_remote_audit=0,
        call_runner=calls,
    )
    assert not succeeded
    assert runtime["task_state"] == "DEGRADED"
    assert runtime["restart_count"] == 1
    assert "TimeoutError" in runtime["last_error"]
    _, _, retried = await _run_archive_maintenance_cycle(
        archive, Store(), runtime, last_compaction=0, last_remote_audit=0,
        call_runner=calls,
    )
    assert not retried and archive.calls == 1


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


class _ScannerProviderFeed:
    def __init__(
        self, name: str, *, blocked: bool = False, degraded: bool = False,
    ) -> None:
        self.provider_name = name
        self.venue = name
        self.blocked = blocked
        self.degraded = degraded
        self.instrument_calls = 0
        self.candle_calls = 0
        self.candle_symbols: list[str] = []
        self.region_blocked_until_at = "2099-01-01T00:00:00+00:00" if blocked else None
        self._candles = _ScannerFeed()

    async def instruments(self):
        self.instrument_calls += 1
        if self.blocked:
            raise ProviderRegionBlockedError(self.provider_name, 451)
        if self.degraded:
            raise ConnectionResetError(f"{self.provider_name} transient reset")
        return [{
            "symbol": f"R{index}-USDT", "status": "TRADING",
            "contract_type": "PERPETUAL", "quote_volume": 100_000_000 - index,
            "change_24h_pct": 8 if index == 0 else 0,
        } for index in range(5)]

    async def candles(self, symbol: str):
        self.candle_calls += 1
        self.candle_symbols.append(symbol)
        return await self._candles.candles(symbol)

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_scanner_binance_451_uses_healthy_okx_universe_and_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _sqlite(monkeypatch, tmp_path)
    binance = _ScannerProviderFeed("BINANCE", blocked=True)
    okx = _ScannerProviderFeed("OKX")
    monitor = PumpDumpMonitor(feed=ProviderFailoverBroadFeed((binance, okx)))
    monitor.universe_limit = 5

    result = await monitor.check_once()

    assert result["status"] == "ok"
    assert result["provider_coverage"] == "PARTIAL"
    assert result["providers"]["BINANCE"]["state"] == "REGION_BLOCKED"
    assert result["providers"]["OKX"]["state"] == "HEALTHY"
    assert result["universe"] == result["baseline_ready_symbols"] == 5
    assert result["successfully_fetched"] == 5 and okx.candle_calls == 5
    assert result["current_stage"] == "idle"
    assert set(okx.candle_symbols) == {f"R{index}USDT" for index in range(5)}

    from services.operational_runtime import OperationalHealthRepository
    OperationalHealthRepository().heartbeat(
        instance_id="test", state="RUNNING",
        started_at=datetime.now(timezone.utc).isoformat(), child_states={},
    )
    health = monitor.repository.home_stats(telegram_id=0)
    assert health["scanner_status"] == "DEGRADED"
    assert health["provider_operability"] == "RUNNING_PARTIAL"


@pytest.mark.asyncio
async def test_scanner_okx_progresses_when_binance_blocked_and_bingx_degraded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _sqlite(monkeypatch, tmp_path)
    binance = _ScannerProviderFeed("BINANCE", blocked=True)
    okx = _ScannerProviderFeed("OKX")
    bingx = _ScannerProviderFeed("BINGX", degraded=True)
    monitor = PumpDumpMonitor(feed=ProviderFailoverBroadFeed((binance, okx, bingx)))
    monitor.universe_limit = 5

    result = await monitor.check_once()

    assert result["status"] == "ok" and result["successfully_fetched"] == 5
    assert okx.candle_calls == 5
    assert result["providers"]["BINANCE"]["state"] == "REGION_BLOCKED"
    assert result["providers"]["BINGX"]["state"] == "DEGRADED"
    assert result["providers"]["OKX"]["state"] == "HEALTHY"


@pytest.mark.asyncio
async def test_scanner_reports_unavailable_only_when_all_viable_providers_fail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _sqlite(monkeypatch, tmp_path)
    binance = _ScannerProviderFeed("BINANCE", blocked=True)
    okx = _ScannerProviderFeed("OKX", degraded=True)
    monitor = PumpDumpMonitor(feed=ProviderFailoverBroadFeed((binance, okx)))

    result = await monitor.check_once()

    assert result["status"] == "degraded"
    assert result["reason"] == "SCANNER_MINIMUM_PROVIDER_COVERAGE_UNAVAILABLE"
    assert result["provider_coverage"] == "UNAVAILABLE"
    assert result["current_stage"] == "provider_coverage_unavailable"
    assert result["universe"] == result["baseline_ready_symbols"] == 0
    assert result["providers"]["BINANCE"]["state"] == "REGION_BLOCKED"
    assert result["providers"]["OKX"]["state"] == "DEGRADED"

    from services.operational_runtime import OperationalHealthRepository
    OperationalHealthRepository().heartbeat(
        instance_id="test", state="RUNNING",
        started_at=datetime.now(timezone.utc).isoformat(), child_states={},
    )
    health = monitor.repository.home_stats(telegram_id=0)
    assert health["scanner_status"] == "FAILED"
    assert health["provider_operability"] == "UNAVAILABLE"


@pytest.mark.asyncio
async def test_okx_scanner_feed_normalizes_universe_and_one_minute_history() -> None:
    class Provider:
        async def _load_swap_instruments(self):
            return {"BTC-USDT-SWAP": {"state": "live"}}

        async def _request(self, path, params):
            assert path == "/api/v5/market/tickers" and params == {"instType": "SWAP"}
            return {"data": [{
                "instId": "BTC-USDT-SWAP", "last": "50000", "open24h": "49000",
                "volCcy24h": "1000", "volCcyQuote": "50000000",
            }]}

        async def get_klines(self, symbol, interval, limit):
            assert (symbol, interval, limit) == ("BTCUSDT", "1m", 241)
            start = datetime.now(timezone.utc) - timedelta(minutes=240)
            rows = [SimpleNamespace(
                time=start + timedelta(minutes=index), open=100 + index,
                high=101 + index, low=99 + index, close=100.5 + index,
                volume=10, volCcyQuote=1005 + index,
            ) for index in range(241)]

            class Frame:
                def itertuples(self, index=False):
                    assert index is False
                    return iter(rows)

            return Frame()

    feed = OKXFuturesBroadFeed(provider=Provider())
    instruments = await feed.instruments()
    candles = await feed.candles("BTCUSDT")

    assert len(instruments) == 1
    assert instruments[0]["symbol"] == "BTCUSDT"
    assert instruments[0]["status"] == "TRADING"
    assert instruments[0]["contract_type"] == "PERPETUAL"
    assert instruments[0]["quote_volume"] == 50_000_000.0
    assert instruments[0]["change_24h_pct"] == pytest.approx(2.0408163265306123)
    assert len(candles) == 241
    assert candles[-1].quote_volume == 1245
    assert candles[-1].trade_count is None


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
