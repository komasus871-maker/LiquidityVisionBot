from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from database import database as db
from services.webhook_server import WebhookServer


class _DuplicateColumn(Exception):
    pgcode = "42701"


def test_concurrent_postgres_add_column_race_is_recoverable(monkeypatch):
    calls: list[str] = []

    class Connection:
        postgres = True

        def execute(self, sql, params=()):
            calls.append(sql)
            if sql.startswith("ALTER TABLE"):
                raise _DuplicateColumn("column already exists")

    monkeypatch.setattr(db, "_columns", lambda *_: set())
    db._add_column(Connection(), "live_exchange_accounts", "sync_stage", "TEXT")

    assert calls == [
        "SAVEPOINT add_column_guard",
        "ALTER TABLE live_exchange_accounts ADD COLUMN sync_stage TEXT",
        "ROLLBACK TO SAVEPOINT add_column_guard",
        "RELEASE SAVEPOINT add_column_guard",
    ]


@pytest.mark.asyncio
async def test_webhook_overload_returns_retryable_response(monkeypatch):
    monkeypatch.setenv("WEBHOOK_MAX_ACTIVE_UPDATES", "1")
    bot = SimpleNamespace(token="123456:abcdefghijklmnopqrstuvwxyzABCDE")
    server = WebhookServer(bot=bot, dispatcher=SimpleNamespace())
    server._tasks.add(object())

    request = SimpleNamespace(
        headers={"X-Telegram-Bot-Api-Secret-Token": server.secret},
        json=lambda: _async_value({"update_id": 9911}),
    )
    response = await server.webhook_handler(request)

    assert response.status == 503
    assert response.text == "busy"
    assert 9911 not in server._recent_ids


@pytest.mark.asyncio
async def test_webhook_secret_rejection_logs_only_safe_metadata(caplog):
    bot = SimpleNamespace(token="123456:abcdefghijklmnopqrstuvwxyzABCDE")
    server = WebhookServer(bot=bot, dispatcher=SimpleNamespace())
    supplied_secret = "wrong-secret-must-never-appear-in-logs"
    request = SimpleNamespace(
        headers={"X-Telegram-Bot-Api-Secret-Token": supplied_secret},
    )

    with caplog.at_level("WARNING"):
        response = await server.webhook_handler(request)

    assert response.status == 403
    assert "WEBHOOK_AUTH_REJECTED" in caplog.text
    assert "header_present=True" in caplog.text
    assert f"header_length={len(supplied_secret)}" in caplog.text
    assert f"expected_length={len(server.secret)}" in caplog.text
    assert "fingerprint" not in caplog.text
    assert supplied_secret not in caplog.text
    assert server.secret not in caplog.text


def test_webhook_uses_render_managed_secret_when_configured(monkeypatch):
    render_seed = "B0jrphAPOY7pg92AN0c9MN4yecczLMdwnx4OkA1KFUk="
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("WEBHOOK_SECRET_SEED", render_seed)
    server = WebhookServer(
        bot=SimpleNamespace(token="123456:abcdefghijklmnopqrstuvwxyzABCDE"),
        dispatcher=SimpleNamespace(),
    )

    assert server.secret == hashlib.sha256(render_seed.encode("utf-8")).hexdigest()
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,256}", server.secret)
    blueprint = Path("render.yaml").read_text(encoding="utf-8")
    assert "- key: WEBHOOK_SECRET_SEED\n        generateValue: true" in blueprint


def test_explicit_webhook_secret_must_be_telegram_compatible(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "invalid/base64=")

    with pytest.raises(RuntimeError, match="must contain only"):
        WebhookServer(
            bot=SimpleNamespace(token="123456:abcdefghijklmnopqrstuvwxyzABCDE"),
            dispatcher=SimpleNamespace(),
        )


@pytest.mark.asyncio
async def test_webhook_startup_registers_and_accepts_same_derived_secret(monkeypatch, caplog):
    render_seed = "B0jrphAPOY7pg92AN0c9MN4yecczLMdwnx4OkA1KFUk="
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("WEBHOOK_SECRET_SEED", render_seed)
    monkeypatch.setenv("PORT", "0")

    class Bot:
        token = "123456:abcdefghijklmnopqrstuvwxyzABCDE"

        async def set_webhook(self, **kwargs):
            self.registration = kwargs

        async def get_webhook_info(self):
            return SimpleNamespace(
                url=self.registration["url"],
                pending_update_count=3,
                last_error_message=None,
            )

    bot = Bot()
    dispatcher = SimpleNamespace(resolve_used_update_types=lambda: ["message"])
    server = WebhookServer(bot=bot, dispatcher=dispatcher)

    with caplog.at_level(logging.INFO):
        await server.start()
        try:
            request = SimpleNamespace(
                headers={"X-Telegram-Bot-Api-Secret-Token": server.secret},
                json=lambda: _async_value({}),
            )
            response = await server.webhook_handler(request)
        finally:
            await server.stop()

    assert bot.registration["secret_token"] == server.secret
    assert bot.registration["drop_pending_updates"] is False
    assert response.status == 400
    assert render_seed not in caplog.text
    assert server.secret not in caplog.text


async def _async_value(value):
    return value
