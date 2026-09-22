from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.forward_runtime_state import ForwardRuntimeStateRepository
from services.market_terminal import render_system_status
from services.operational_runtime import OperationalHealthRepository
from services.webhook_server import WebhookServer


class _Bot:
    token = "123456:TEST_WEB_HEALTH_TOKEN"

    async def get_webhook_info(self):
        raise AssertionError("Render health must not call the Telegram API")


def _ready_server() -> WebhookServer:
    server = WebhookServer(bot=_Bot(), dispatcher=SimpleNamespace())
    server._web_ready = True
    server._web_health_reason = "WEB_SERVICEABLE"
    return server


async def _health(server: WebhookServer) -> tuple[int, dict]:
    response = await server.health_handler(SimpleNamespace())
    return response.status, json.loads(response.text)


@pytest.mark.asyncio
async def test_render_health_is_200_with_forward_worker_unavailable(monkeypatch) -> None:
    monkeypatch.setenv("FORWARD_WORKER_EXPECTED", "true")
    monkeypatch.setattr(
        ForwardRuntimeStateRepository,
        "health",
        lambda _: {"state": "UNAVAILABLE", "heartbeat_at": None},
    )

    status, payload = await _health(_ready_server())

    assert status == 200
    assert payload["status"] == "ok" and payload["reason"] == "WEB_SERVICEABLE"


@pytest.mark.asyncio
async def test_render_health_is_200_with_operational_worker_warming(monkeypatch) -> None:
    monkeypatch.setenv("OPERATIONAL_WORKER_EXPECTED", "true")
    monkeypatch.setattr(
        OperationalHealthRepository,
        "health",
        lambda _: {"state": "STARTING", "heartbeat_at": None},
    )

    status, payload = await _health(_ready_server())

    assert status == 200
    assert payload["checks"]["telegram_webhook_handler"] == "ready"


@pytest.mark.asyncio
async def test_render_health_is_200_with_r2_degraded(monkeypatch) -> None:
    monkeypatch.setenv("FORWARD_OBJECT_STORAGE_ENABLED", "true")
    monkeypatch.setattr(
        ForwardRuntimeStateRepository,
        "health",
        lambda _: {"state": "RUNNING", "storage": {"object_storage_status": "DEGRADED"}},
    )

    status, payload = await _health(_ready_server())

    assert status == 200
    assert payload["checks"]["web_local_initialization"] == "complete"


@pytest.mark.asyncio
async def test_render_health_is_200_with_telegram_delivery_auth_degraded() -> None:
    server = _ready_server()
    server._registration_state = "AUTH_INVALID"
    server._registration_error = "TELEGRAM_REPORTED_DELIVERY_403"

    status, payload = await _health(server)

    assert status == 200
    assert payload["status"] == "ok"
    assert payload["reason"] == "WEB_SERVICEABLE"
    assert payload["checks"]["telegram_webhook_registration"] == "AUTH_INVALID"
    assert payload["checks"]["telegram_webhook_delivery"] == "degraded"
    assert payload["checks"]["telegram_webhook_error"] == "TELEGRAM_REPORTED_DELIVERY_403"


@pytest.mark.asyncio
async def test_render_health_fails_until_web_initialization_completes(caplog) -> None:
    server = WebhookServer(bot=_Bot(), dispatcher=SimpleNamespace())

    with caplog.at_level(logging.INFO):
        status, payload = await _health(server)

    assert status == 503
    assert payload["status"] == "unavailable"
    assert payload["reason"] == "WEB_INITIALIZATION_INCOMPLETE"
    assert "status=503" in caplog.text
    assert "reason=WEB_INITIALIZATION_INCOMPLETE" in caplog.text
    assert "latency_ms=" in caplog.text


@pytest.mark.asyncio
async def test_webhook_url_mismatch_is_degraded_but_keeps_local_liveness(monkeypatch) -> None:
    class BrokenBot:
        token = "123456:TEST_WEB_HEALTH_TOKEN"

        async def set_webhook(self, **kwargs) -> bool:
            self.requested_url = kwargs["url"]
            return True

        async def get_webhook_info(self):
            return SimpleNamespace(
                url="https://wrong.example/telegram/webhook",
                pending_update_count=0,
                last_error_message=None,
            )

    dispatcher = SimpleNamespace(resolve_used_update_types=lambda: [])
    server = WebhookServer(bot=BrokenBot(), dispatcher=dispatcher)
    monkeypatch.setenv("PORT", "0")

    await server.start()
    try:
        status, payload = await _health(server)
        assert status == 200
        assert payload["reason"] == "WEB_SERVICEABLE"
        assert payload["checks"]["telegram_webhook_registration"] == "URL_MISMATCH"
        assert payload["checks"]["telegram_webhook_delivery"] == "not_ready"
        assert payload["checks"]["telegram_webhook_error"] == "WEBHOOK_URL_MISMATCH"
    finally:
        await server.stop()


def test_terminal_system_keeps_distributed_degradation_detail() -> None:
    now = datetime.now(timezone.utc).isoformat()
    rendered = render_system_status(
        {
            "state": "STALE",
            "heartbeat_at": None,
            "venues": {"BINANCE": {"last_error": "feed unavailable"}},
            "storage": {
                "disk_status": "WARNING",
                "usage_percent": 80.0,
                "free_gb": 10.0,
                "estimated_days_remaining": 2.0,
                "object_storage_status": "DEGRADED",
                "pending_partitions": 3,
                "pending_upload_bytes": 1024,
                "estimated_spool_hours_remaining": 4.0,
                "checksum_failures": 1,
            },
        },
        {"state": "STARTING", "heartbeat_at": now},
    )

    assert "Collector: <b>STALE</b>" in rendered
    assert "Product worker: <b>STARTING</b>" in rendered
    assert "Object archive: <b>DEGRADED</b>" in rendered
    assert "1 checksum failures" in rendered
    assert "BINANCE: DEGRADED" in rendered


def test_render_blueprint_uses_fast_health_route() -> None:
    blueprint = Path("render.yaml").read_text(encoding="utf-8")
    assert "healthCheckPath: /health" in blueprint
