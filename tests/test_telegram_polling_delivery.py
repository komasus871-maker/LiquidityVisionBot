from __future__ import annotations

import asyncio
import importlib
import json
import threading
from types import SimpleNamespace

import pytest

from services.telegram_polling import SingletonTelegramPoller, TELEGRAM_POLLER_LEASE
from services.webhook_server import WebhookServer


class _PollingBot:
    token = "123456:polling-test-token"

    def __init__(self) -> None:
        self.deleted: list[dict[str, object]] = []

    async def delete_webhook(self, **kwargs):
        self.deleted.append(kwargs)
        return True

    async def set_webhook(self, **_kwargs):
        raise AssertionError("polling delivery must not call setWebhook")


class _PollingDispatcher:
    def __init__(self, active: dict[str, int] | None = None) -> None:
        self.started = asyncio.Event()
        self.stopped = asyncio.Event()
        self.call: dict[str, object] | None = None
        self.active = active

    def resolve_used_update_types(self):
        return ["message"]

    async def start_polling(self, bot, **kwargs):
        self.call = {"bot": bot, **kwargs}
        if self.active is not None:
            self.active["current"] += 1
            self.active["maximum"] = max(self.active["maximum"], self.active["current"])
        self.started.set()
        try:
            await self.stopped.wait()
        finally:
            if self.active is not None:
                self.active["current"] -= 1

    async def stop_polling(self):
        self.stopped.set()


def test_telegram_delivery_mode_defaults_to_webhook_and_validates(monkeypatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "123456:delivery-mode-test")
    bot_module = importlib.import_module("bot")
    monkeypatch.delenv("TELEGRAM_DELIVERY_MODE", raising=False)
    assert bot_module.telegram_delivery_mode() == "webhook"
    monkeypatch.setenv("TELEGRAM_DELIVERY_MODE", "polling")
    assert bot_module.telegram_delivery_mode() == "polling"
    monkeypatch.setenv("TELEGRAM_DELIVERY_MODE", "invalid")
    with pytest.raises(RuntimeError, match="TELEGRAM_DELIVERY_MODE"):
        bot_module.telegram_delivery_mode()


@pytest.mark.asyncio
async def test_polling_http_mode_skips_registration_and_keeps_health_live(monkeypatch) -> None:
    monkeypatch.setenv("PORT", "0")

    class Bot(_PollingBot):
        async def get_webhook_info(self):
            raise AssertionError("polling HTTP mode must not inspect webhook registration")

    server = WebhookServer(bot=Bot(), dispatcher=SimpleNamespace())
    await server.start(register_webhook=False)
    try:
        response = await server.health_handler(SimpleNamespace())
        payload = json.loads(response.text)
        assert response.status == 200
        assert payload["reason"] == "WEB_SERVICEABLE"
        assert payload["checks"]["telegram_webhook_registration"] == "POLLING_MODE"
        assert payload["checks"]["telegram_webhook_delivery"] == "polling"
        assert server._registration_monitor_task is None
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_webhook_http_mode_default_still_registers_without_dropping_updates(monkeypatch) -> None:
    monkeypatch.setenv("PORT", "0")

    class Bot:
        token = "123456:webhook-test-token"

        async def get_webhook_info(self):
            return SimpleNamespace(
                url="https://liquidityvisionbot-1.onrender.com/telegram/webhook",
                pending_update_count=2,
                last_error_message=None,
                last_error_date=None,
            )

        async def set_webhook(self, **kwargs):
            self.registration = kwargs
            return True

    bot = Bot()
    dispatcher = SimpleNamespace(resolve_used_update_types=lambda: ["message"])
    server = WebhookServer(bot=bot, dispatcher=dispatcher)
    await server.start()
    try:
        assert bot.registration["secret_token"] == server.secret
        assert bot.registration["drop_pending_updates"] is False
        assert server._registration_state == "REGISTERED"
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_poller_waits_for_lease_deletes_webhook_and_releases_cleanly() -> None:
    attempts = 0
    releases: list[tuple[str, str]] = []

    def acquire(name: str, owner: str, _ttl: int) -> bool:
        nonlocal attempts
        attempts += 1
        assert name == TELEGRAM_POLLER_LEASE and owner == "poller-a"
        return attempts >= 2

    def release(name: str, owner: str) -> None:
        releases.append((name, owner))

    bot = _PollingBot()
    dispatcher = _PollingDispatcher()
    shutdown = asyncio.Event()
    poller = SingletonTelegramPoller(
        bot,
        dispatcher,
        acquire=acquire,
        release=release,
        owner_id="poller-a",
        wait_base_seconds=0.01,
        wait_max_seconds=0.01,
    )
    task = asyncio.create_task(poller.run(shutdown))
    await asyncio.wait_for(dispatcher.started.wait(), timeout=1)
    shutdown.set()
    await asyncio.wait_for(task, timeout=1)

    assert attempts >= 2
    assert bot.deleted == [{"drop_pending_updates": False}]
    assert dispatcher.call is not None
    assert dispatcher.call["handle_signals"] is False
    assert dispatcher.call["close_bot_session"] is False
    assert releases == [(TELEGRAM_POLLER_LEASE, "poller-a")]


@pytest.mark.asyncio
async def test_two_pollers_never_overlap_during_rolling_handoff() -> None:
    lock = threading.Lock()
    lease = {"owner": None}
    active = {"current": 0, "maximum": 0}

    def acquire(_name: str, owner: str, _ttl: int) -> bool:
        with lock:
            if lease["owner"] in {None, owner}:
                lease["owner"] = owner
                return True
            return False

    def release(_name: str, owner: str) -> None:
        with lock:
            if lease["owner"] == owner:
                lease["owner"] = None

    first_dispatcher = _PollingDispatcher(active)
    second_dispatcher = _PollingDispatcher(active)
    first_shutdown, second_shutdown = asyncio.Event(), asyncio.Event()
    first = SingletonTelegramPoller(
        _PollingBot(), first_dispatcher, acquire=acquire, release=release,
        owner_id="old-instance", wait_base_seconds=0.01, wait_max_seconds=0.01,
    )
    second = SingletonTelegramPoller(
        _PollingBot(), second_dispatcher, acquire=acquire, release=release,
        owner_id="new-instance", wait_base_seconds=0.01, wait_max_seconds=0.01,
    )

    first_task = asyncio.create_task(first.run(first_shutdown))
    await asyncio.wait_for(first_dispatcher.started.wait(), timeout=1)
    second_task = asyncio.create_task(second.run(second_shutdown))
    await asyncio.sleep(0.15)
    assert not second_dispatcher.started.is_set()

    first_shutdown.set()
    await asyncio.wait_for(first_task, timeout=1)
    await asyncio.wait_for(second_dispatcher.started.wait(), timeout=1)
    second_shutdown.set()
    await asyncio.wait_for(second_task, timeout=1)

    assert active == {"current": 0, "maximum": 1}
    assert lease["owner"] is None
