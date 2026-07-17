from __future__ import annotations

import json
import os
import platform
from datetime import datetime, timezone
from typing import Any

from database.database import connect, database_backend, get_runtime_states, persistent_database, ping_database

APP_VERSION = os.getenv("APP_VERSION", "7.2.0")
_STARTED_AT = datetime.now(timezone.utc)


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _scalar(conn, sql: str, params: tuple = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return 0
    if isinstance(row, dict):
        return int(next(iter(row.values())) or 0)
    return int(row[0] or 0)


def collect_runtime_diagnostics(*, stale_after_seconds: int | None = None) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    stale_after = stale_after_seconds or int(os.getenv("WORKER_STALE_AFTER", "900"))
    db = ping_database()

    workers: list[dict[str, Any]] = []
    stale_workers: list[str] = []
    for state in get_runtime_states():
        item = dict(state)
        success_at = _parse_time(item.get("last_success_at"))
        age = int((now - success_at).total_seconds()) if success_at else None
        item["age_seconds"] = age
        item["stale"] = age is None or age > stale_after
        try:
            item["details"] = json.loads(item.get("details_json") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            item["details"] = {}
        item.pop("details_json", None)
        workers.append(item)
        if item["stale"]:
            stale_workers.append(str(item.get("worker_name")))

    with connect() as conn:
        counts = {
            "users": _scalar(conn, "SELECT COUNT(*) FROM users"),
            "watchlist_items": _scalar(conn, "SELECT COUNT(*) FROM user_watchlist"),
            "observations": _scalar(conn, "SELECT COUNT(*) FROM analysis_observations"),
            "open_signals": _scalar(conn, "SELECT COUNT(*) FROM signals WHERE status IN ('WATCHING','TRIGGERED','ACTIVE','TP1','TP2')"),
            "active_trades": _scalar(conn, "SELECT COUNT(*) FROM signals WHERE status IN ('ACTIVE','TP1','TP2')"),
            "closed_signals": _scalar(conn, "SELECT COUNT(*) FROM signals WHERE status IN ('TP3','STOP','CLOSED','INVALIDATED','EXPIRED')"),
            "watch_errors": _scalar(conn, "SELECT COUNT(*) FROM watch_states WHERE consecutive_errors > 0"),
        }
        duplicate_open_plans = _scalar(conn, """
            SELECT COUNT(*) FROM (
                SELECT COALESCE(owner_telegram_id,0), symbol, timeframe
                FROM signals
                WHERE status IN ('WATCHING','TRIGGERED','ACTIVE','TP1','TP2')
                GROUP BY COALESCE(owner_telegram_id,0), symbol, timeframe
                HAVING COUNT(*) > 1
            ) x
        """)
        impossible_active = _scalar(conn, """
            SELECT COUNT(*) FROM signals
            WHERE status IN ('ACTIVE','TP1','TP2')
              AND (activated_at IS NULL OR effective_stop IS NULL)
        """)

    integrity = {
        "ok": duplicate_open_plans == 0 and impossible_active == 0,
        "duplicate_open_plans": duplicate_open_plans,
        "active_without_activation_or_stop": impossible_active,
    }
    status = "ok"
    if not db.get("ok") or not integrity["ok"]:
        status = "degraded"
    elif stale_workers:
        status = "warning"

    return {
        "status": status,
        "service": "Liquidity Vision Intelligence",
        "version": APP_VERSION,
        "environment": os.getenv("RENDER_SERVICE_NAME") or os.getenv("ENVIRONMENT", "local"),
        "database_backend": database_backend(),
        "persistent_database": persistent_database(),
        "database": db,
        "counts": counts,
        "integrity": integrity,
        "workers": workers,
        "stale_workers": stale_workers,
        "worker_stale_after_seconds": stale_after,
        "uptime_seconds": max(0, int((now - _STARTED_AT).total_seconds())),
        "python": platform.python_version(),
        "timestamp": now.isoformat(),
    }
