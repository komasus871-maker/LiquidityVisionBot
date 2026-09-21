from __future__ import annotations

import hashlib
import hmac
import inspect
import json
import os
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlencode

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import database.database as database
from services.operational_runtime import OperationalHealthRepository
from services.pump_dump_scanner import (
    SCANNER_ALERT_TYPES, PumpDumpScanner, ScannerRepository, ScannerSettings,
    build_symbol_snapshot,
)
from services.webhook_server import WebhookServer


def _sqlite(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(database, "USE_POSTGRES", False)
    monkeypatch.setattr(database, "REQUIRE_PERSISTENT_DB", False)
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "DATABASE_NAME", tmp_path / "runtime.sqlite3")
    database.create_tables()


def _signed_init_data(token: str, now: int) -> str:
    values = {
        "auth_date": str(now), "query_id": "AAE",
        "user": json.dumps({"id": 42, "first_name": "Ada"}, separators=(",", ":")),
    }
    check = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


def test_operational_worker_capabilities_and_authority_boundary() -> None:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = ".codex-test-deps;."
    result = subprocess.run(
        [sys.executable, "-m", "tools.run_operational_worker", "--print-capabilities"],
        cwd=Path.cwd(), env=environment, text=True, capture_output=True, check=True,
    )
    report = json.loads(result.stdout)
    assert report["economic_authority"] == "PAPER_ONLY"
    assert report["live_execution_authority"] is False
    assert report["telegram_polling"] is False and report["telegram_webhook"] is False
    assert report["forward_raw_collection"] is False
    assert {"signal_tracker", "watch_engine", "copy_execution", "pump_dump_monitor"} <= set(
        report["components"]
    )
    source = Path("tools/run_operational_worker.py").read_text(encoding="utf-8")
    assert "start_polling" not in source and "WebhookServer" not in source
    assert "ForwardCollectorSupervisor" not in source and "LiveCopyWorker" not in source


def test_operational_heartbeat_is_shared_and_bounded(monkeypatch, tmp_path: Path) -> None:
    _sqlite(monkeypatch, tmp_path)
    repository = OperationalHealthRepository()
    repository.heartbeat(
        instance_id="test-instance", state="RUNNING",
        started_at="2026-09-20T12:00:00+00:00",
        child_states={"signal_tracker": {"processed_count": 2}},
    )
    health = repository.health()
    assert health and health["state"] == "RUNNING"
    assert health["child_states"]["signal_tracker"]["processed_count"] == 2
    assert "candidate" not in json.dumps(health).lower()


@pytest.mark.asyncio
async def test_terminal_routes_auth_and_all_real_state_pages(monkeypatch, tmp_path: Path) -> None:
    _sqlite(monkeypatch, tmp_path)
    token = "123456:TEST"

    class Bot:
        def __init__(self) -> None:
            self.token = token

    server = WebhookServer(bot=Bot(), dispatcher=object())
    app = web.Application()
    app.router.add_get("/terminal", server.terminal_handler)
    app.router.add_get("/api/terminal/{page}", server.terminal_api_handler)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        page = await client.get("/terminal")
        assert page.status == 200
        html = await page.text()
        assert "telegram-web-app.js" in html and '"portfolio"' in html and '"alerts"' in html
        denied = await client.get("/api/terminal/overview")
        assert denied.status == 403
        now = int(datetime.now(timezone.utc).timestamp())
        headers = {"X-Telegram-Init-Data": _signed_init_data(token, now)}
        for name in (
            "overview", "markets", "scanner", "signals", "order-flow", "derivatives",
            "paper", "portfolio", "risk", "alerts", "shadow", "economics", "system",
        ):
            response = await client.get(f"/api/terminal/{name}", headers=headers)
            assert response.status == 200, name
            payload = await response.json()
            assert payload["status"] == "ok" and payload["page"] == name
            assert payload["economic_authority"] is False and payload["bounded"] is True
            if name == "system":
                assert "forward_disk_status" in payload["items"][0]
                assert "forward_disk_estimated_days_remaining" in payload["items"][0]
                assert "forward_object_storage_status" in payload["items"][0]
                assert "forward_spool_hours_remaining" in payload["items"][0]
    finally:
        await client.close()


def _candles() -> list:
    from services.pump_dump_scanner import Candle
    start = datetime(2026, 9, 20, tzinfo=timezone.utc)
    rows = []
    for index in range(241):
        price = 100 + (index % 7) * .01
        if index >= 238:
            price += (index - 237) * .4
        volume = 1_000 if index < 240 else 5_000
        rows.append(Candle(start + timedelta(minutes=index), price, price + .1, price - .1,
                           price, volume, 100 + index))
    return rows


def test_adaptive_scanner_classification_quality_and_history(monkeypatch, tmp_path: Path) -> None:
    _sqlite(monkeypatch, tmp_path)
    snapshot = build_symbol_snapshot(
        symbol="TESTUSDT", venue="BINANCE", candles=_candles(),
        quote_volume_24h=100_000_000,
        observed_at=datetime(2026, 9, 20, 4, 0, tzinfo=timezone.utc),
    )
    assert snapshot.robust_zscore and snapshot.historical_percentile
    enriched = replace(
        snapshot, oi_change_pct=6.0, taker_imbalance=.7, cvd=2_000_000,
        spread_pct=.03, book_imbalance=.2, cross_venue_diff_pct=.02,
        liquidations_usd=50_000, data_quality="VALID",
    )
    settings = ScannerSettings(move_threshold_pct=.5)
    events = PumpDumpScanner().detect(enriched, settings)
    assert events and events[0]["market_state"] == "LEVERAGED_BREAKOUT"
    assert events[0]["alert_type"] == "OI_BUILDUP"
    assert events[0]["classification"] == "MARKET_ALERT"
    assert events[0]["economic_authority"] is False
    alert = ScannerRepository().admit(events[0], settings, telegram_id=42)
    assert alert and alert.market_state == "LEVERAGED_BREAKOUT" and alert.reasons
    context = ScannerRepository.historical_context("TESTUSDT", "LEVERAGED_BREAKOUT")
    assert context["similar_episode_count"] == 1
    assert context["sample_adequate"] is False and context["predictive_probability"] is None
    stale = replace(enriched, freshness_seconds=181)
    assert PumpDumpScanner().detect(stale, settings) == []
    disabled = replace(settings, enabled_alert_types=tuple(
        item for item in SCANNER_ALERT_TYPES if item != "OI_BUILDUP"
    ))
    assert PumpDumpScanner().detect(enriched, disabled) == []
    ScannerRepository.save_settings(42, disabled)
    assert "OI_BUILDUP" not in ScannerRepository.settings(42).enabled_alert_types


def test_render_has_exactly_one_owner_for_each_continuous_role() -> None:
    text = Path("render.yaml").read_text(encoding="utf-8")
    assert text.count("startCommand: python bot.py") == 1
    assert text.count("startCommand: python -m tools.run_operational_worker") == 1
    assert text.count("startCommand: python -m tools.run_forward_microstructure_collector") == 1
    operational = text.split("name: liquidityvision-operational-worker", 1)[1].split(
        "name: liquidityvision-forward-worker", 1
    )[0]
    assert 'PUMP_SCANNER_ENABLED\n        value: "true"' in operational
    assert 'MICROSTRUCTURE_COLLECTION_ENABLED\n        value: "false"' in operational
    for flag in (
        "LIVE_EXECUTION_ENABLED", "LIVE_DISPATCHER_ENABLED",
        "ALLOW_USER_LIVE_CONNECTIONS", "BINGX_PRODUCTION_ADAPTER_ALLOWED",
    ):
        assert text.count(flag) == 3


def test_broad_scanner_promotes_only_shortlist_to_forward_enrichment() -> None:
    from services.pump_dump_monitor import PumpDumpMonitor
    source = inspect.getsource(PumpDumpMonitor._check_once_owned)
    assert "shortlisted" in source
    assert "latest_states(tuple(sorted(shortlisted)))" in source
    assert source.index("self.detector.detect(snapshot, settings)") < source.index(
        "latest_states(tuple(sorted(shortlisted)))"
    )


def test_authoritative_functionality_matrix_covers_every_control() -> None:
    from keyboards.main_menu import main_keyboard
    from services.command_catalog import FUNCTION_REGISTRY
    from services.functionality_audit import (
        REPLY_CONTROLS, TERMINAL_PAGES, full_functionality_matrix, runtime_ownership_matrix,
    )

    rows = full_functionality_matrix()
    ids = {row["id"] for row in rows}
    assert {item.id for item in FUNCTION_REGISTRY} <= ids
    assert len([item for item in rows if str(item["id"]).startswith("reply.")]) == len(REPLY_CONTROLS)
    assert len(ids) == len(rows)
    assert {f"terminal.{page}" for page in TERMINAL_PAGES} <= ids
    assert {f"scanner.alert.{item.lower()}" for item in SCANNER_ALERT_TYPES} <= ids
    assert "worker.signal_tracker" in ids and "migration.product" in ids
    required = {
        "entry_point", "handler", "service_layer", "data_source", "state_owner",
        "persistent_background_dependency", "database_tables", "expected_output",
        "failure_mode", "fallback", "runtime_class", "runtime_owner",
    }
    assert all(required <= set(row) for row in rows)
    callback_rows = [row for row in rows if str(row["id"]).startswith("callback.")]
    assert callback_rows and all(row["handler"] != "callback router (filter-derived)" for row in callback_rows)
    labels = {button.text for line in main_keyboard().keyboard for button in line}
    assert labels == set(REPLY_CONTROLS)
    owners = runtime_ownership_matrix()
    assert len({row["owner"] for row in owners}) == len(owners)
    assert all(row["singleton"] for row in owners)


def test_no_placeholder_product_callbacks_remain() -> None:
    source = Path("handlers/menu.py").read_text(encoding="utf-8")
    assert "находится в разработке" not in source
    assert "пока недоступен" not in source
    assert "has expired" in source


def test_terminal_public_url_is_https_fail_closed(monkeypatch) -> None:
    from services.webhook_server import resolve_public_base_url
    monkeypatch.setenv("WEBHOOK_BASE_URL", "http://example.com")
    with pytest.raises(RuntimeError, match="valid HTTPS"):
        resolve_public_base_url()
    monkeypatch.setenv("WEBHOOK_BASE_URL", "https://example.com/")
    assert resolve_public_base_url() == "https://example.com"


def test_market_cache_is_ttl_and_size_bounded() -> None:
    from services.cache import Cache
    cache = Cache(max_entries=8)
    for index in range(20):
        cache.set(f"key-{index}", index, ttl=60)
    assert len(cache.cache) == 8
    assert cache.get("key-0") is None
    assert cache.get("key-19") == 19


@pytest.mark.asyncio
async def test_concurrent_market_requests_are_singleflight() -> None:
    import pandas as pd
    from types import SimpleNamespace
    from services.market import Market

    class Provider:
        calls = 0

        async def get_klines(self, **_kwargs):
            self.calls += 1
            await asyncio.sleep(.02)
            return pd.DataFrame({"timestamp": [1], "open": [1.0], "high": [1.0],
                                 "low": [1.0], "close": [1.0], "volume": [1.0]})

    class Integrity:
        def prepare_market_frame(self, frame, **_kwargs):
            return SimpleNamespace(frame=frame, valid=True)

    provider = Provider()
    left, right = Market(), Market()
    left.provider = right.provider = provider
    left.integrity = right.integrity = Integrity()
    await asyncio.gather(
        left.get_klines("SINGLEFLIGHTTESTUSDT", "1h", 1),
        right.get_klines("SINGLEFLIGHTTESTUSDT", "1h", 1),
    )
    assert provider.calls == 1
