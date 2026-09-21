from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import database.database as database
from handlers import pump_scanner as scanner_handler
from services.pump_dump_scanner import (
    OUTCOME_HORIZON_MINUTES,
    Candle,
    PumpDumpScanner,
    ScannerRepository,
    ScannerSettings,
    build_symbol_snapshot,
)
from services.telegram_webapp import (
    WebAppAuthError,
    issue_terminal_session,
    terminal_html,
    validate_terminal_session,
)
from services.webhook_server import WebhookServer


def _sqlite(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(database, "USE_POSTGRES", False)
    monkeypatch.setattr(database, "REQUIRE_PERSISTENT_DB", False)
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "DATABASE_NAME", tmp_path / "master-product.sqlite3")
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


@pytest.mark.asyncio
async def test_terminal_auth_exchange_issues_bounded_bearer_session(monkeypatch, tmp_path: Path) -> None:
    _sqlite(monkeypatch, tmp_path)
    token = "123456:TEST"

    class Bot:
        def __init__(self) -> None:
            self.token = token

    server = WebhookServer(bot=Bot(), dispatcher=object())
    app = web.Application()
    app.router.add_post("/api/terminal/auth", server.terminal_auth_handler)
    app.router.add_get("/api/terminal/{page}", server.terminal_api_handler)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        now = int(datetime.now(timezone.utc).timestamp())
        auth = await client.post("/api/terminal/auth", json={
            "init_data": _signed_init_data(token, now), "js_ready": True,
        })
        assert auth.status == 200
        payload = await auth.json()
        assert payload["status"] == "ok" and payload["expires_in_seconds"] == 900
        session = payload["session_token"]
        assert validate_terminal_session(session, token).telegram_id == 42
        response = await client.get(
            "/api/terminal/overview", headers={"Authorization": f"Bearer {session}"},
        )
        assert response.status == 200
        assert (await response.json())["economic_authority"] is False
        rejected = await client.get(
            "/api/terminal/overview", headers={"Authorization": f"Bearer {session}x"},
        )
        assert rejected.status == 403
    finally:
        await client.close()


def test_terminal_session_expiry_and_frontend_never_has_unbounded_auth_state() -> None:
    token = "123456:TEST"
    identity = SimpleNamespace(telegram_id=42, username=None, first_name="Ada", auth_date=1000)
    session = issue_terminal_session(identity, token, ttl_seconds=60, now=1000)
    with pytest.raises(WebAppAuthError, match="expired terminal session") as error:
        validate_terminal_session(session, token, now=1061)
    assert error.value.code == "AUTH_EXPIRED"
    html = terminal_html()
    assert "AUTH_TIMEOUT" in html and "BOOTSTRAP_FAILED" in html
    assert "Retry" in html and "Close / Back" in html
    assert "setTimeout" in html and "AbortController" in html
    assert 'join("\\n")' in html
    assert "TERMINAL_BOOTSTRAP_COMPLETE" in html


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["⚡ Scanner", "/scanner"])
async def test_scanner_visible_entry_and_command_always_open_home(
    monkeypatch, tmp_path: Path, entry: str,
) -> None:
    _sqlite(monkeypatch, tmp_path)

    class Message:
        text = entry
        from_user = SimpleNamespace(id=42)

        def __init__(self) -> None:
            self.answers = []

        async def answer(self, text, **kwargs):
            self.answers.append((text, kwargs))

    message = Message()
    await scanner_handler.pump_scan(message)
    text, kwargs = message.answers[-1]
    assert "LIQUIDITY VISION SCANNER" in text
    assert "Unknown view" not in text
    labels = [button.text for row in kwargs["reply_markup"].inline_keyboard for button in row]
    assert {"🔥 Hot Now", "🧨 Short Squeezes", "🩸 Long Squeezes", "🚀 Breakout Ignition"} <= set(labels)


def _snapshot(at: datetime, price_shift: float = 0.0):
    candles = []
    start = at - timedelta(minutes=240)
    for index in range(241):
        price = 100 + index * .002
        if index >= 236:
            price += (index - 235) * .5
        price += price_shift
        candles.append(Candle(
            start + timedelta(minutes=index), price, price + .05, price - .05,
            price, 1000 if index < 240 else 5000, 100 + index,
        ))
    return build_symbol_snapshot(
        symbol="TESTUSDT", venue="BINANCE", candles=candles,
        quote_volume_24h=100_000_000, observed_at=at,
    )


def test_scanner_frozen_snapshot_and_causal_fixed_horizon_outcomes(monkeypatch, tmp_path: Path) -> None:
    _sqlite(monkeypatch, tmp_path)
    decision_at = datetime(2026, 9, 20, 4, 0, tzinfo=timezone.utc)
    snapshot = _snapshot(decision_at)
    settings = ScannerSettings(move_threshold_pct=.2, cooldown_seconds=1)
    event = PumpDumpScanner().detect(snapshot, settings)[0]
    alert = ScannerRepository().admit(event, settings, telegram_id=42)
    assert alert is not None and alert.economic_authority is False
    with database.connect() as conn:
        frozen_before = conn.execute(
            "SELECT snapshot_json FROM market_anomaly_events WHERE event_id=?", (alert.event_id,),
        ).fetchone()[0]
        scheduled = conn.execute(
            "SELECT horizon_minutes,status FROM scanner_outcome_labels WHERE event_id=? ORDER BY horizon_minutes",
            (alert.event_id,),
        ).fetchall()
    assert [int(row[0]) for row in scheduled] == list(OUTCOME_HORIZON_MINUTES)
    assert {row[1] for row in scheduled} == {"PENDING"}

    later = replace(snapshot, observed_at=decision_at + timedelta(minutes=241),
                    price=snapshot.price * 1.04)
    advanced = ScannerRepository.advance_outcomes(later)
    assert advanced == {"updated": 7, "finalized": 7}
    with database.connect() as conn:
        frozen_after = conn.execute(
            "SELECT snapshot_json FROM market_anomaly_events WHERE event_id=?", (alert.event_id,),
        ).fetchone()[0]
        labels = [dict(row) for row in conn.execute(
            "SELECT * FROM scanner_outcome_labels WHERE event_id=?", (alert.event_id,),
        ).fetchall()]
    assert frozen_after == frozen_before
    assert all(row["status"] == "LABELED" and int(row["observation_count"]) == 1 for row in labels)
    assert all(float(row["net_directional_return_pct"]) < float(row["directional_return_pct"])
               for row in labels)
    cohort = ScannerRepository.outcome_attribution(horizon_minutes=15, minimum_samples=30)
    assert cohort["sample_size"] == 1 and cohort["conclusion_status"] == "INSUFFICIENT_SAMPLE"
    assert cohort["predictive_probability"] is None and cohort["economic_authority"] is False


def test_schema_v2_contains_scanner_outcome_ledger(monkeypatch, tmp_path: Path) -> None:
    _sqlite(monkeypatch, tmp_path)
    with database.connect() as conn:
        marker = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='scanner_outcome_labels'"
        ).fetchone()
    assert int(marker) == 2 and table is not None


def test_economic_dashboard_separates_paper_value_and_infrastructure_cost(
    monkeypatch, tmp_path: Path,
) -> None:
    _sqlite(monkeypatch, tmp_path)
    monkeypatch.setenv("INFRA_RENDER_COMPUTE_USD_MONTH", "39")
    monkeypatch.setenv("INFRA_RENDER_DISK_USD_MONTH", "12.5")
    monkeypatch.setenv("INFRA_DATABASE_USD_MONTH", "7")
    monkeypatch.setenv("INFRA_R2_STORAGE_USD_PER_GB_MONTH", ".015")
    monkeypatch.setenv("INFRA_R2_OPERATIONS_USD_MONTH", "0")
    from services.economic_value_dashboard import EconomicValueDashboard

    report = EconomicValueDashboard().report(42)
    assert report["economic_authority"] is False
    assert report["paper_value_is_real_profit"] is False
    assert report["infrastructure"]["cost_status"] == "COMPLETE"
    assert report["infrastructure"]["estimated_total_monthly_cost_usd"] == 58.5
    assert report["net_value_status"] == "PAPER_SIMULATION_ONLY"
