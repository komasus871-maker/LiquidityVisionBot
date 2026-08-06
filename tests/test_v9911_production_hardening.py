from __future__ import annotations

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


async def _async_value(value):
    return value
