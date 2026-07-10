"""Small HTTP server required by Render Web Services.

The Telegram bot continues to use long polling.  This server only exposes
health endpoints so Render can detect the bound ``PORT`` and keep the deploy
alive instead of terminating it after the port-scan timeout.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from aiohttp import web


_STARTED_AT = datetime.now(timezone.utc)


async def health_handler(_: web.Request) -> web.Response:
    """Return a lightweight health response for Render and uptime checks."""
    now = datetime.now(timezone.utc)
    uptime_seconds = max(0, int((now - _STARTED_AT).total_seconds()))
    payload: dict[str, Any] = {
        "status": "ok",
        "service": "Liquidity Vision Intelligence",
        "uptime_seconds": uptime_seconds,
        "timestamp": now.isoformat(),
    }
    return web.json_response(payload)


async def root_handler(_: web.Request) -> web.Response:
    """Human-readable root endpoint."""
    return web.Response(
        text="Liquidity Vision Intelligence is online.",
        content_type="text/plain",
    )


async def start_health_server() -> web.AppRunner:
    """Bind Render's PORT and return the runner for graceful cleanup."""
    host = os.getenv("HEALTH_HOST", "0.0.0.0")
    port_raw = os.getenv("PORT", os.getenv("HEALTH_PORT", "10000"))

    try:
        port = int(port_raw)
    except ValueError as exc:
        raise RuntimeError(f"Invalid PORT value: {port_raw!r}") from exc

    app = web.Application()
    app.router.add_get("/", root_handler)
    app.router.add_get("/health", health_handler)
    app.router.add_get("/healthz", health_handler)

    runner = web.AppRunner(app, access_log=None)
    await runner.setup()

    site = web.TCPSite(runner, host=host, port=port)
    await site.start()
    logging.info("Health server listening on http://%s:%s", host, port)
    return runner
