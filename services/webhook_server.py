"""Telegram webhook and health server for Render Web Service deployments."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.types import Update

from database.database import database_backend, persistent_database
from services.forward_runtime_state import ForwardRuntimeStateRepository
from services.pump_dump_scanner import ScannerRepository, resource_budget
from services.telegram_webapp import (
    WebAppAuthError,
    issue_terminal_session,
    terminal_html,
    validate_init_data,
    validate_terminal_session,
)
from database.database import connect

_STARTED_AT = datetime.now(timezone.utc)


def resolve_public_base_url() -> str:
    """Resolve the public HTTPS base URL Render exposes for this service."""
    explicit = (
        os.getenv("WEBHOOK_BASE_URL")
        or os.getenv("RENDER_EXTERNAL_URL")
        or os.getenv("PUBLIC_BASE_URL")
    )
    if explicit:
        candidate = explicit.rstrip("/")
        parsed = urlparse(candidate)
        if parsed.scheme != "https" or not parsed.netloc:
            raise RuntimeError("Telegram WebApp/Webhook public base URL must be valid HTTPS")
        return candidate

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
        self.max_active_updates = max(1, int(os.getenv("WEBHOOK_MAX_ACTIVE_UPDATES", "100")))
        self._recent_ids: set[int] = set()
        self._recent_order: deque[int] = deque(maxlen=1000)
        self._update_latencies_ms: deque[float] = deque(maxlen=500)
        self._update_errors = 0
        self._updates_processed = 0
        self._web_ready = False
        self._web_health_reason = "WEB_INITIALIZATION_INCOMPLETE"
        self.maintenance_callback = maintenance_callback
        self._maintenance_lock = asyncio.Lock()
        self.maintenance_token = os.getenv("MONITOR_CRON_SECRET", "").strip()

    def _webapp_identity(self, request: web.Request):
        authorization = request.headers.get("Authorization", "")
        if authorization.startswith("Bearer "):
            return validate_terminal_session(authorization[7:].strip(), self.bot.token)
        value = request.headers.get("X-Telegram-Init-Data", "") or request.query.get("initData", "")
        return validate_init_data(value, self.bot.token)

    @staticmethod
    def _json(payload: dict[str, Any], status: int = 200) -> web.Response:
        import json
        return web.Response(text=json.dumps(payload, default=str, ensure_ascii=False),
                            status=status, content_type="application/json")

    async def terminal_handler(self, _: web.Request) -> web.Response:
        return web.Response(text=terminal_html(), content_type="text/html")

    async def terminal_auth_handler(self, request: web.Request) -> web.Response:
        started = time.perf_counter()
        logging.info("TERMINAL_AUTH_START")
        try:
            payload = await request.json()
        except Exception:
            logging.info("AUTH_RESPONSE status=400 code=AUTH_REJECTED latency_ms=%.2f",
                         (time.perf_counter() - started) * 1000)
            logging.warning("TERMINAL_AUTH_FAILED code=AUTH_REJECTED")
            return self._json({"status": "error", "code": "AUTH_REJECTED"}, 400)
        init_data = str(payload.get("init_data") or "") if isinstance(payload, dict) else ""
        if isinstance(payload, dict) and payload.get("js_ready") is True:
            logging.info("TELEGRAM_JS_READY")
        if init_data:
            logging.info("INIT_DATA_PRESENT")
        logging.info("AUTH_REQUEST_SENT")
        try:
            identity = validate_init_data(init_data, self.bot.token)
        except WebAppAuthError as exc:
            status = 401 if exc.code == "AUTH_EXPIRED" else 403
            logging.info("AUTH_RESPONSE status=%s code=%s latency_ms=%.2f", status, exc.code,
                         (time.perf_counter() - started) * 1000)
            logging.warning("TERMINAL_AUTH_FAILED code=%s", exc.code)
            return self._json({"status": "error", "code": exc.code}, status)
        token = issue_terminal_session(identity, self.bot.token)
        logging.info("AUTH_RESPONSE status=200 code=AUTH_SUCCESS latency_ms=%.2f",
                     (time.perf_counter() - started) * 1000)
        logging.info("AUTH_SUCCESS telegram_id=%s", identity.telegram_id)
        return self._json({
            "status": "ok",
            "session_token": token,
            "expires_in_seconds": 900,
            "user": {
                "telegram_id": identity.telegram_id,
                "first_name": identity.first_name,
            },
        })

    async def terminal_api_handler(self, request: web.Request) -> web.Response:
        try:
            identity = self._webapp_identity(request)
        except WebAppAuthError as exc:
            logging.warning("TERMINAL_AUTH_FAILED code=%s", exc.code)
            return self._json({"status": "forbidden", "code": exc.code}, 403)
        page = request.match_info["page"]
        logging.info("TERMINAL_BOOTSTRAP_STARTED telegram_id=%s page=%s",
                     identity.telegram_id, page)
        state = ForwardRuntimeStateRepository()
        rows = state.latest_states(("BTCUSDT", "ETHUSDT", "SOLUSDT"))
        metadata = {}
        if page in {"overview", "markets"}:
            items = [{"symbol": row["symbol"], "venue": row["venue"],
                      "market_state": row["market_state"], "data_quality": row["data_quality"],
                      "observed_at": row["observed_at"]} for row in rows[:12]]
        elif page in {"order-flow", "derivatives"}:
            items = []
            for row in rows[:12]:
                snap = row["snapshot"]
                details = (snap.get("derivatives") or {}) if page == "derivatives" else {
                    "book": snap.get("book") or {}, "trade_flow": snap.get("trade_flow") or {},
                    "liquidations": snap.get("liquidations") or {},
                }
                items.append({"symbol": row["symbol"], "venue": row["venue"], **details,
                              "observed_at": row["observed_at"]})
        elif page == "scanner":
            from services.pump_dump_scanner import DISCOVERY_MODES
            selected_view = request.query.get("view", "HOT_NOW").strip().upper().replace("-", "_")
            if selected_view not in DISCOVERY_MODES:
                return self._json({"status": "invalid_view", "code": "UNKNOWN_SCANNER_VIEW"}, 400)
            items = ScannerRepository.recent(
                50, telegram_id=identity.telegram_id, mode=selected_view,
            )
            metadata = {
                "selected_view": selected_view,
                "available_views": list(DISCOVERY_MODES),
            }
        elif page == "signals":
            with connect() as connection:
                records = connection.execute(
                    """SELECT id,symbol,timeframe,side,status,confidence,current_price,updated_at
                       FROM signals WHERE owner_telegram_id=? ORDER BY updated_at DESC LIMIT 50""",
                    (identity.telegram_id,),
                ).fetchall()
            scanner_rows = ScannerRepository.recent(200, telegram_id=identity.telegram_id)
            scanner_context = {}
            for scanner_row in scanner_rows:
                normalized = str(scanner_row.get("symbol") or "").removesuffix("USDT")
                scanner_context.setdefault(normalized, {
                    "phase": scanner_row.get("phase"),
                    "market_state": scanner_row.get("market_state"),
                    "evidence_quality": scanner_row.get("evidence_quality"),
                    "observed_at": scanner_row.get("observed_at"),
                    "economic_authority": False,
                })
            items = []
            for record in records:
                item = dict(record)
                item["scanner_context"] = scanner_context.get(
                    str(item.get("symbol") or "").upper().removesuffix("USDT"),
                    "UNAVAILABLE",
                )
                items.append(item)
        elif page == "paper":
            with connect() as connection:
                records = connection.execute("""SELECT symbol,status,side,quantity,average_entry,
                    last_price,realized_pnl,unrealized_pnl,total_commission,opened_at,updated_at
                    FROM paper_execution_positions
                    WHERE telegram_id=? ORDER BY opened_at DESC LIMIT 50""",
                    (identity.telegram_id,)).fetchall()
            items = [dict(record) for record in records]
        elif page in {"portfolio", "risk"}:
            from services.execution_portfolio import ExecutionPortfolioEngine
            snapshot = ExecutionPortfolioEngine().snapshot(identity.telegram_id).as_dict()
            if page == "portfolio":
                keys = (
                    "starting_balance", "net_equity", "open_positions", "symbols",
                    "gross_notional", "net_notional", "realized_gross_pnl",
                    "unrealized_pnl", "commissions", "net_realized_pnl",
                )
            else:
                keys = (
                    "confirmed_heat_r", "risk_complete", "risk_partial", "risk_missing",
                    "risk_invalid", "unresolved_risk_count", "resolved", "rejection_count",
                    "cooldown_symbols", "authority",
                )
            items = [{key: snapshot.get(key) for key in keys}]
        elif page == "alerts":
            from services.user_preferences import UserPreferenceService
            preferences = UserPreferenceService().get(identity.telegram_id)
            scanner = ScannerRepository.settings(identity.telegram_id)
            with connect() as connection:
                records = connection.execute(
                    """SELECT symbol,timeframe,alert_type,severity,status,occurred_at,delivered_at,
                       suppressed_reason FROM intelligence_alert_events WHERE telegram_id=?
                       ORDER BY occurred_at DESC LIMIT 25""",
                    (identity.telegram_id,),
                ).fetchall()
            items = [{
                "notification_categories": preferences.get("notification_categories"),
                "alert_verbosity": preferences.get("alert_verbosity"),
                "scanner_enabled": scanner.enabled,
                "scanner_minimum_severity": scanner.minimum_severity.value,
                "scanner_cooldown_seconds": scanner.cooldown_seconds,
            }, *[dict(record) for record in records]]
        elif page == "shadow":
            health = state.health() or {"state": "NOT_STARTED", "execution_authority": False}
            items = [{key: value for key, value in health.items()
                      if key not in {"candidate_ids_json"}}]
        elif page == "economics":
            from services.economic_value_dashboard import EconomicValueDashboard
            items = [EconomicValueDashboard().report(identity.telegram_id)]
        elif page == "system":
            from services.operational_runtime import OperationalHealthRepository
            forward_health = state.health() or {"state": "NOT_STARTED"}
            operational_health = OperationalHealthRepository().health() or {"state": "NOT_STARTED"}
            storage = forward_health.get("storage") or {}
            items = [{
                "database_backend": database_backend(),
                "persistent_database": persistent_database(),
                "forward_state": forward_health.get("state"),
                "forward_heartbeat_at": forward_health.get("heartbeat_at"),
                "operational_state": operational_health.get("state"),
                "operational_heartbeat_at": operational_health.get("heartbeat_at"),
                "operational_rss_mb": operational_health.get("rss_mb"),
                "forward_disk_status": storage.get("disk_status"),
                "forward_disk_usage_percent": storage.get("usage_percent"),
                "forward_disk_free_gb": storage.get("free_gb"),
                "forward_disk_estimated_days_remaining": storage.get("estimated_days_remaining"),
                "forward_disk_thresholds": storage.get("thresholds"),
                "forward_spool_pending_bytes": storage.get("pending_upload_bytes"),
                "forward_spool_hours_remaining": storage.get("estimated_spool_hours_remaining"),
                "forward_object_storage_status": storage.get("object_storage_status"),
                "forward_object_last_upload_at": storage.get("last_successful_upload_at"),
                "forward_object_upload_latency_ms": storage.get("last_upload_latency_ms"),
                "forward_object_pending_partitions": storage.get("pending_partitions"),
                "forward_object_failed_uploads": storage.get("failed_uploads"),
                "forward_object_verified_bytes": storage.get("remotely_verified_bytes"),
                "forward_object_current_30_day_bytes": storage.get("current_30_day_stored_bytes"),
                "forward_object_oldest_evidence_ts_ms": storage.get("oldest_evidence_ts_ms"),
                "forward_object_newest_evidence_ts_ms": storage.get("newest_evidence_ts_ms"),
                "forward_integrity_checksum_failures": storage.get("checksum_failures"),
                "forward_integrity_missing_remote_objects": storage.get("missing_remote_objects"),
                "forward_integrity_manifest_inconsistencies": storage.get("manifest_inconsistencies"),
                "live_execution_enabled": os.getenv(
                    "LIVE_EXECUTION_ENABLED", "false"
                ).strip().lower() in {"1", "true", "yes", "on"},
            }]
        else:
            logging.warning("TERMINAL_BOOTSTRAP_FAILED telegram_id=%s page=%s code=NOT_FOUND",
                            identity.telegram_id, page)
            return self._json({"status": "not_found"}, 404)
        logging.info("TERMINAL_BOOTSTRAP_COMPLETE telegram_id=%s page=%s items=%s",
                     identity.telegram_id, page, len(items))
        return self._json({"status": "ok", "page": page, "classification": "MARKET_INTELLIGENCE",
                           "economic_authority": False, "bounded": True, "resource_budget": resource_budget(),
                           "items": items, **metadata})

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
        started = time.perf_counter()
        try:
            await self.dispatcher.feed_update(self.bot, update)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._update_errors += 1
            logging.exception("Unhandled exception while processing webhook update %s", update.update_id)
        finally:
            self._updates_processed += 1
            self._update_latencies_ms.append((time.perf_counter() - started) * 1000)

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

        # Do not acknowledge work that this process cannot retain. Telegram
        # will retry a 503, whereas returning 200 here would silently drop the
        # update during an overload or slow downstream dependency.
        if len(self._tasks) >= self.max_active_updates:
            logging.warning(
                "Webhook update capacity reached: active=%s limit=%s update_id=%s",
                len(self._tasks), self.max_active_updates, update.update_id,
            )
            return web.Response(status=503, text="busy")

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
        """Return the bounded web-process readiness contract used by Render.

        Distributed subsystem diagnostics deliberately remain on the Terminal
        System surfaces. A worker, archive, provider, or research degradation
        must not prevent an otherwise serviceable Telegram web process from
        being promoted during a rolling deploy.
        """
        started = time.perf_counter()
        ready = self._web_ready
        status = 200 if ready else 503
        reason = self._web_health_reason
        now = datetime.now(timezone.utc)
        payload = {
            "status": "ok" if ready else "unavailable",
            "service": "Liquidity Vision Intelligence Telegram web",
            "mode": "webhook",
            "ready": ready,
            "reason": reason,
            "checks": {
                "http_event_loop": "operational",
                "telegram_webhook_handler": "ready" if ready else "not_ready",
                "web_local_initialization": "complete" if ready else "incomplete",
            },
            "uptime_seconds": max(0, int((now - _STARTED_AT).total_seconds())),
            "timestamp": now.isoformat(),
        }
        latency_ms = (time.perf_counter() - started) * 1000
        logging.info(
            "Render health check status=%s reason=%s latency_ms=%.2f",
            status,
            reason,
            latency_ms,
        )
        return web.json_response(payload, status=status)

    async def root_handler(self, _: web.Request) -> web.Response:
        return web.Response(text="Liquidity Vision Intelligence webhook is online.")

    async def start(self) -> None:
        host = os.getenv("HEALTH_HOST", "0.0.0.0")
        try:
            port = int(os.getenv("PORT", os.getenv("HEALTH_PORT", "10000")))
        except ValueError as exc:
            raise RuntimeError("PORT must be an integer") from exc

        self._web_ready = False
        self._web_health_reason = "WEB_INITIALIZATION_IN_PROGRESS"
        try:
            app = web.Application(client_max_size=2 * 1024 * 1024)
            app.router.add_get("/", self.root_handler)
            app.router.add_get("/health", self.health_handler)
            app.router.add_get("/healthz", self.health_handler)
            app.router.add_get("/terminal", self.terminal_handler)
            app.router.add_post("/api/terminal/auth", self.terminal_auth_handler)
            app.router.add_get("/api/terminal/{page}", self.terminal_api_handler)
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
        except Exception:
            self._web_ready = False
            self._web_health_reason = "WEB_INITIALIZATION_FAILED"
            raise
        self._web_ready = True
        self._web_health_reason = "WEB_SERVICEABLE"

    async def stop(self) -> None:
        # Do not delete the webhook on rolling deploy shutdown. The next
        # Render instance uses the same URL and remains reachable.
        self._web_ready = False
        self._web_health_reason = "WEB_SHUTTING_DOWN"
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self.runner is not None:
            await self.runner.cleanup()
