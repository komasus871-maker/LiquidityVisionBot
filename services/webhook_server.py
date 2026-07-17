"""Telegram webhook and health server for Render Web Service deployments."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from collections import deque
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.types import Update

from database.database import database_backend, persistent_database
from services.runtime_diagnostics import collect_runtime_diagnostics

_STARTED_AT = datetime.now(timezone.utc)


def resolve_public_base_url() -> str:
    """Resolve the public HTTPS base URL Render exposes for this service."""
    explicit = (
        os.getenv("WEBHOOK_BASE_URL")
        or os.getenv("RENDER_EXTERNAL_URL")
        or os.getenv("PUBLIC_BASE_URL")
    )
    if explicit:
        return explicit.rstrip("/")

    service_name = os.getenv("RENDER_SERVICE_NAME", "").strip()
    if service_name:
        return f"https://{service_name}.onrender.com"

    # Safe fallback for the user's existing Render service. It can be
    # overridden with WEBHOOK_BASE_URL if the service name changes.
    return "https://liquidityvisionbot-1.onrender.com"


def webhook_secret(bot_token: str) -> str:
    configured = os.getenv("WEBHOOK_SECRET", "").strip()
    if configured:
        return configured
    return hashlib.sha256(bot_token.encode("utf-8")).hexdigest()


class WebhookServer:
    def __init__(
        self,
        bot: Bot,
        dispatcher: Dispatcher,
        path: str = "/telegram/webhook",
        maintenance_callback: Callable[[], Awaitable[dict[str, Any]]] | None = None,
    ) -> None:
        self.bot = bot
        self.dispatcher = dispatcher
        self.path = path
        self.base_url = resolve_public_base_url()
        self.url = f"{self.base_url}{self.path}"
        self.secret = webhook_secret(bot.token)
        self.runner: web.AppRunner | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        self._recent_ids: set[int] = set()
        self._recent_order: deque[int] = deque(maxlen=1000)
        self.maintenance_callback = maintenance_callback
        self._maintenance_lock = asyncio.Lock()
        self.maintenance_token = os.getenv("MONITOR_CRON_SECRET", "").strip()

    def _remember_update(self, update_id: int) -> bool:
        """Return False when Telegram retries an update we already accepted."""
        if update_id in self._recent_ids:
            return False
        if len(self._recent_order) == self._recent_order.maxlen:
            oldest = self._recent_order.popleft()
            self._recent_ids.discard(oldest)
        self._recent_order.append(update_id)
        self._recent_ids.add(update_id)
        return True

    async def _process_update(self, update: Update) -> None:
        try:
            await self.dispatcher.feed_update(self.bot, update)
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("Unhandled exception while processing webhook update %s", update.update_id)

    def _task_done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logging.error("Webhook task failed", exc_info=(type(exc), exc, exc.__traceback__))

    async def webhook_handler(self, request: web.Request) -> web.Response:
        provided = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if provided != self.secret:
            return web.Response(status=403, text="forbidden")

        try:
            payload = await request.json()
            update = Update.model_validate(payload, context={"bot": self.bot})
        except Exception:
            logging.exception("Invalid Telegram webhook payload")
            return web.Response(status=400, text="bad request")

        if not self._remember_update(update.update_id):
            return web.Response(text="duplicate")

        # Acknowledge Telegram immediately. Heavy analysis continues in a
        # tracked background task, preventing webhook retries and UI freezes.
        task = asyncio.create_task(self._process_update(update))
        self._tasks.add(task)
        task.add_done_callback(self._task_done)
        return web.Response(text="ok")


    async def maintenance_handler(self, request: web.Request) -> web.Response:
        """Run one monitor cycle from an external scheduler.

        Free Render services sleep when idle, so in-process loops cannot run
        while the service is suspended. A trusted external cron can call this
        endpoint every few minutes to wake the service and execute one full
        watch/observation/signal cycle.
        """
        if not self.maintenance_callback:
            return web.json_response({"status": "disabled"}, status=404)
        provided = request.headers.get("X-Monitor-Secret", "") or request.query.get("token", "")
        if not self.maintenance_token or provided != self.maintenance_token:
            return web.json_response({"status": "forbidden"}, status=403)
        if self._maintenance_lock.locked():
            return web.json_response({"status": "busy"}, status=202)
        async with self._maintenance_lock:
            try:
                result = await self.maintenance_callback()
                return web.json_response({"status": "ok", **result})
            except Exception as exc:
                logging.exception("Manual monitor cycle failed")
                return web.json_response({"status": "error", "detail": str(exc)}, status=500)

    async def health_handler(self, _: web.Request) -> web.Response:
        try:
            report = await asyncio.to_thread(collect_runtime_diagnostics)
            info = await self.bot.get_webhook_info()
            report.update({
                "mode": "webhook",
                "webhook_url": info.url,
                "pending_update_count": info.pending_update_count,
                "last_error_message": info.last_error_message,
                "active_update_tasks": len(self._tasks),
            })
            http_status = 503 if report["status"] == "degraded" else 200
            return web.json_response(report, status=http_status)
        except Exception as exc:
            logging.exception("Health diagnostics failed")
            return web.json_response({
                "status": "degraded",
                "service": "Liquidity Vision Intelligence",
                "database_backend": database_backend(),
                "persistent_database": persistent_database(),
                "error": str(exc),
            }, status=503)

    async def root_handler(self, _: web.Request) -> web.Response:
        return web.Response(text="Liquidity Vision Intelligence webhook is online.")

    async def start(self) -> None:
        host = os.getenv("HEALTH_HOST", "0.0.0.0")
        try:
            port = int(os.getenv("PORT", os.getenv("HEALTH_PORT", "10000")))
        except ValueError as exc:
            raise RuntimeError("PORT must be an integer") from exc

        app = web.Application(client_max_size=2 * 1024 * 1024)
        app.router.add_get("/", self.root_handler)
        app.router.add_get("/health", self.health_handler)
        app.router.add_get("/healthz", self.health_handler)
        app.router.add_post(self.path, self.webhook_handler)
        app.router.add_post("/internal/monitor", self.maintenance_handler)
        app.router.add_get("/internal/monitor", self.maintenance_handler)

        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, host=host, port=port).start()
        logging.info("HTTP server listening on http://%s:%s", host, port)

        await self.bot.set_webhook(
            url=self.url,
            secret_token=self.secret,
            allowed_updates=self.dispatcher.resolve_used_update_types(),
            drop_pending_updates=False,
            max_connections=20,
        )
        info = await self.bot.get_webhook_info()
        logging.info(
            "Webhook active: %s (pending=%s, last_error=%r)",
            info.url,
            info.pending_update_count,
            info.last_error_message,
        )
        if info.url != self.url:
            raise RuntimeError(f"Telegram webhook mismatch: expected {self.url}, got {info.url}")

    async def stop(self) -> None:
        # Do not delete the webhook on rolling deploy shutdown. The next
        # Render instance uses the same URL and remains reachable.
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self.runner is not None:
            await self.runner.cleanup()
