from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import database.database as database
from handlers import pump_scanner as scanner_handler
from services.forward_event_store import EventType, RawMarketEvent, Venue
from services.forward_event_store import AppendOnlyEventStore
from services.forward_public_collectors import BinancePublicConnector, ForwardCollectorSupervisor
from services.operational_runtime import OperationalHealthRepository
from services.pump_dump_monitor import PumpDumpMonitor
from services.pump_dump_scanner import (
    DISCOVERY_MODES, Candle, PumpDumpScanner, ScannerRepository, ScannerSettings,
    build_symbol_snapshot,
)
from services.telegram_webapp import issue_terminal_session
from services.webhook_server import WebhookServer
from tools.benchmark_scanner_radar import benchmark


def _sqlite(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(database, "USE_POSTGRES", False)
    monkeypatch.setattr(database, "REQUIRE_PERSISTENT_DB", False)
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "DATABASE_NAME", tmp_path / "scanner-data-plane.sqlite3")
    database.create_tables()


def _candles(move_pct: float = 0.0, *, observed_at: datetime | None = None) -> list[Candle]:
    end = observed_at or datetime.now(timezone.utc)
    start = end - timedelta(minutes=240)
    rows: list[Candle] = []
    for index in range(241):
        price = 100 + index * .001
        if index >= 236:
            price *= 1 + (move_pct / 100) * ((index - 235) / 5)
        rows.append(Candle(
            opened_at=start + timedelta(minutes=index), open=price,
            high=price * 1.001, low=price * .999, close=price,
            quote_volume=5_000_000 if index == 240 else 1_000_000,
            trade_count=1_000 + index,
        ))
    return rows


class _Feed:
    def __init__(self, move_pct: float = 8.0) -> None:
        self.move_pct = move_pct
        self.closed = False

    async def instruments(self):
        return [{
            "symbol": f"S{index}USDT", "status": "TRADING",
            "contract_type": "PERPETUAL", "quote_volume": 100_000_000 - index,
            "change_24h_pct": self.move_pct if index == 0 else 0,
        } for index in range(5)]

    async def candles(self, symbol: str):
        return _candles(self.move_pct if symbol == "S0USDT" else 0)

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_broad_radar_runs_without_subscribers_and_persists_global_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _sqlite(monkeypatch, tmp_path)
    monitor = PumpDumpMonitor(feed=_Feed())
    monitor.universe_limit = 5
    result = await monitor.check_once()

    assert result["status"] == "ok"
    assert result["subscribers"] == 0
    assert result["universe"] == result["successfully_fetched"] == 5
    assert result["failed_symbol_count"] == 0
    assert result["baseline_ready_symbols"] == 5
    assert result["global_events_created"] >= 1
    with database.connect() as connection:
        event = connection.execute(
            "SELECT telegram_id,economic_authority FROM market_anomaly_events ORDER BY id LIMIT 1"
        ).fetchone()
        labels = connection.execute("SELECT COUNT(*) FROM scanner_outcome_labels").fetchone()[0]
        settings = connection.execute("SELECT COUNT(*) FROM scanner_user_settings").fetchone()[0]
        runtime = connection.execute(
            "SELECT details_json FROM runtime_state WHERE worker_name=?", (monitor.worker_name,),
        ).fetchone()
    assert tuple(event) == (0, 0)
    assert labels >= 7 and settings == 0
    details = json.loads(runtime[0])
    assert details["pipeline_timestamps"]["episode_engine_completed_at"]
    assert details["cycle_duration_seconds"] >= 0


@pytest.mark.asyncio
async def test_scanner_diagnostics_separate_worker_radar_episode_and_forward_freshness(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _sqlite(monkeypatch, tmp_path)
    monitor = PumpDumpMonitor(feed=_Feed(move_pct=0))
    monitor.universe_limit = 5
    await monitor.check_once()
    now = datetime.now(timezone.utc).isoformat()
    OperationalHealthRepository().heartbeat(
        instance_id="test", state="RUNNING", started_at=now, child_states={},
    )
    report = ScannerRepository.home_stats(telegram_id=42)
    assert report["scanner_status"] == "RUNNING"
    assert report["monitored_symbols"] == 5
    assert report["baseline_ready_symbols"] == 5
    assert report["estimated_readiness_minutes"] == 0
    assert report["scanner_heartbeat_age_seconds"] < 2
    assert report["broad_radar_age_seconds"] < 2
    assert report["episode_engine_age_seconds"] < 2
    assert report["forward_collector_heartbeat_age_seconds"] is None


@pytest.mark.asyncio
async def test_venue_health_requires_complete_critical_channels_and_recovers_from_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    import services.forward_public_collectors as collectors

    clock = {"now": 2_000_000}
    monkeypatch.setattr(collectors, "now_ms", lambda: clock["now"])

    connector = BinancePublicConnector(("BTCUSDT", "ETHUSDT", "SOLUSDT"))
    connector.connected = True
    connector.connection_count = 1
    supervisor = ForwardCollectorSupervisor(
        store=AppendOnlyEventStore(tmp_path / "venue-health.sqlite3"), connectors=[connector],
    )
    supervisor.store.append = lambda event: {"duplicate": True}
    for channel in ("trades", "ticker", "book"):
        supervisor.last_event_ms_by_venue_channel[("BINANCE", channel)] = clock["now"]
        supervisor.symbols_seen_by_venue_channel[("BINANCE", channel)] = {
            "BTCUSDT", "ETHUSDT", "SOLUSDT",
        }
        for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
            supervisor.last_event_ms_by_venue_channel_symbol[(
                "BINANCE", channel, symbol,
            )] = clock["now"]
    supervisor.last_event_ms_by_venue["BINANCE"] = clock["now"]
    health = supervisor.health()["venues"]["BINANCE"]
    assert health["state"] == "HEALTHY"
    assert health["optional_stale_channels"] == ["open_interest", "funding", "liquidations"]

    supervisor.last_event_ms_by_venue_channel_symbol[(
        "BINANCE", "book", "SOLUSDT",
    )] = clock["now"] - 61_000
    degraded = supervisor.health()["venues"]["BINANCE"]
    assert degraded["state"] == "DEGRADED"
    assert degraded["channels"]["book"]["stale_or_missing_symbols"] == 1
    supervisor.last_event_ms_by_venue_channel_symbol[(
        "BINANCE", "book", "SOLUSDT",
    )] = clock["now"]

    connector.last_error = "transient"
    assert supervisor.health()["venues"]["BINANCE"]["state"] == "DEGRADED"
    await supervisor.emit(RawMarketEvent(
        venue=Venue.BINANCE, market="USD_M_PERPETUAL", symbol="BTCUSDT",
        instrument_type="PERPETUAL", event_type=EventType.TRADE,
        exchange_ts_ms=clock["now"], receive_ts_ms=clock["now"],
        price=100, quantity=1, side="BUY", payload={"test": True},
    ))
    assert connector.last_error is None
    assert supervisor.health()["venues"]["BINANCE"]["state"] == "HEALTHY"


def test_deterministic_episode_taxonomy_and_lifecycle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _sqlite(monkeypatch, tmp_path)
    now = datetime.now(timezone.utc)
    snapshot = build_symbol_snapshot(
        symbol="TESTUSDT", venue="BINANCE", candles=_candles(8, observed_at=now),
        change_24h_pct=8, quote_volume_24h=100_000_000, observed_at=now,
    )
    detector = PumpDumpScanner()
    settings = ScannerSettings(cooldown_seconds=900)
    base = detector.detect(snapshot, settings)[0]
    assert base["phase"] in {"IGNITION", "EXPANSION"}

    decisive = replace(
        snapshot,
        changes_pct={window: 8.0 for window in snapshot.changes_pct},
        start_prices={window: snapshot.price / 1.08 for window in snapshot.start_prices},
        return_acceleration_pct=1.0,
    )
    cases = {
        "SHORT_SQUEEZE": replace(decisive, oi_change_pct=-4, liquidations_usd=300_000),
        "LIQUIDITY_VACUUM_MOVE": replace(decisive, book_imbalance=.9),
        "ABSORPTION": replace(decisive, taker_imbalance=-.5, cvd=-2_000_000),
        "EXHAUSTION": replace(decisive, return_acceleration_pct=-1),
    }
    for expected, value in cases.items():
        assert detector.detect(value, settings)[0]["market_state"] == expected

    buildup = replace(
        snapshot, changes_pct={**snapshot.changes_pct, 3: .5},
        start_prices={**snapshot.start_prices, 3: snapshot.price / 1.005},
        robust_zscore={**snapshot.robust_zscore, 3: 3.0}, relative_volume=3.5,
    )
    assert detector.detect(buildup, settings)[0]["phase"] == "BUILDUP"
    ignition_snapshot = replace(
        decisive, changes_pct={window: 3.5 for window in decisive.changes_pct},
        robust_zscore={window: 3.0 for window in decisive.robust_zscore},
    )
    assert detector.detect(ignition_snapshot, settings)[0]["phase"] == "IGNITION"
    assert detector.detect(decisive, settings)[0]["phase"] == "EXPANSION"

    repository = ScannerRepository()
    first = repository.admit(dict(base, phase="BUILDUP", market_state="BUILDUP"), settings)
    assert first is not None
    ignition = repository.admit(dict(
        base, phase="IGNITION", market_state="MOMENTUM_EXPANSION",
        observed_at=now + timedelta(minutes=1),
    ), settings)
    expansion = repository.admit(dict(
        base, phase="EXPANSION", market_state="MOMENTUM_EXPANSION",
        observed_at=now + timedelta(minutes=2), move_pct=base["move_pct"] + 3,
    ), settings)
    assert ignition is not None and expansion is not None
    assert repository.admit(dict(
        base, phase="EXPANSION", market_state="MOMENTUM_EXPANSION",
        observed_at=now + timedelta(minutes=3), move_pct=base["move_pct"] + 3,
    ), settings) is None
    assert repository.close_inactive(older_than=now + timedelta(hours=1)) == 1
    counters = repository.outcome_counters()
    assert counters["snapshots_collected"] == 3
    assert counters["labels_pending"] == 21
    assert counters["cohorts_sample_ready"] == 0


@pytest.mark.asyncio
async def test_settings_buttons_are_primary_unified_scanner_ux(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _sqlite(monkeypatch, tmp_path)

    class Message:
        def __init__(self) -> None:
            self.edits = []

        async def edit_text(self, text, **kwargs):
            self.edits.append((text, kwargs))

    message = Message()
    callback = SimpleNamespace(
        data="pdsettings", from_user=SimpleNamespace(id=42), message=message,
        answer=lambda *args, **kwargs: None,
    )

    async def answer(*args, **kwargs):
        return None

    callback.answer = answer
    await scanner_handler.settings_callback(callback)
    text, kwargs = message.edits[-1]
    assert "SCANNER SETTINGS" in text and "PUMP / DUMP SCANNER" not in text
    labels = [button.text for row in kwargs["reply_markup"].inline_keyboard for button in row]
    assert {"Sensitivity", "Universe", "Windows", "Volume", "Categories", "Muted Symbols"} <= set(labels)
    assert ScannerRepository.subscribers()[0][0] == 42

    callback.data = "scanset:notifications"
    await scanner_handler.settings_control_callback(callback)
    assert ScannerRepository.settings(42).notifications_enabled is False


def test_all_scanner_views_have_informative_empty_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _sqlite(monkeypatch, tmp_path)
    for mode in DISCOVERY_MODES:
        text, rows = scanner_handler._scanner_view_text(42, mode)
        assert rows == []
        assert "No qualifying anomaly episodes" in text
        assert "Scanner:" in text and "Universe:" in text and "Baseline ready:" in text


@pytest.mark.asyncio
async def test_terminal_scanner_uses_same_authoritative_health(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _sqlite(monkeypatch, tmp_path)
    token = "123456:TEST"
    bot = SimpleNamespace(token=token)
    server = WebhookServer(bot=bot, dispatcher=object())
    app = web.Application()
    app.router.add_get("/api/terminal/{page}", server.terminal_api_handler)
    session = issue_terminal_session(
        SimpleNamespace(telegram_id=42, username=None, first_name="Ada", auth_date=1), token,
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.get(
            "/api/terminal/scanner", headers={"Authorization": f"Bearer {session}"},
        )
        payload = await response.json()
        assert response.status == 200
        assert payload["scanner_health"] == ScannerRepository.home_stats(telegram_id=42)
        assert payload["available_views"] == list(DISCOVERY_MODES)
    finally:
        await client.close()


def test_resource_benchmark_covers_preregistered_universe_sizes_without_changing_config() -> None:
    report = benchmark((40, 75, 100, 150, 200))
    assert [row["universe"] for row in report["results"]] == [40, 75, 100, 150, 200]
    assert all(row["wall_seconds"] >= 0 and row["python_peak_mb"] > 0 for row in report["results"])
    assert report["configured_universe_unchanged"] is True
    assert report["provider_rate_limit_certified"] is False
