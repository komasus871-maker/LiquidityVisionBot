from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from database.database import connect


@dataclass(frozen=True)
class ExecutionEvent:
    telegram_id: int
    signal_id: int | None
    event_type: str
    payload: Mapping[str, Any]
    price: float | None = None
    realized_pnl_delta: float = 0.0
    created_at: str | None = None


class ExecutionEventBus:
    """Durable event boundary with optional in-process subscribers.

    Persistence is the source of truth. Subscribers are best-effort observers and
    cannot roll back the committed execution lifecycle.
    """

    def __init__(self) -> None:
        self._subscribers: list[Callable[[ExecutionEvent], None]] = []

    def subscribe(self, handler: Callable[[ExecutionEvent], None]) -> None:
        if handler not in self._subscribers:
            self._subscribers.append(handler)

    def publish(self, event: ExecutionEvent) -> int:
        created_at = event.created_at or datetime.now(timezone.utc).isoformat()
        payload_json = json.dumps(dict(event.payload), ensure_ascii=False, sort_keys=True, default=str)
        with connect() as conn:
            cur = conn.execute(
                """INSERT INTO execution_events(
                       telegram_id,signal_id,event_type,price,realized_pnl_delta,details_json,created_at
                   ) VALUES(?,?,?,?,?,?,?)""",
                (event.telegram_id, event.signal_id, event.event_type, event.price,
                 float(event.realized_pnl_delta), payload_json, created_at),
            )
            event_id = int(cur.lastrowid or 0)
            if not event_id:
                row = conn.execute(
                    "SELECT id FROM execution_events WHERE telegram_id=? AND created_at=? ORDER BY id DESC LIMIT 1",
                    (event.telegram_id, created_at),
                ).fetchone()
                event_id = int(row[0]) if row else 0
        for handler in tuple(self._subscribers):
            try:
                handler(event)
            except Exception:
                continue
        return event_id

    def recent(self, telegram_id: int, *, limit: int = 50, event_type: str | None = None) -> list[dict[str, Any]]:
        safe = max(1, min(int(limit), 200))
        with connect() as conn:
            if event_type:
                rows = conn.execute(
                    f"SELECT * FROM execution_events WHERE telegram_id=? AND event_type=? ORDER BY id DESC LIMIT {safe}",
                    (telegram_id, event_type),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT * FROM execution_events WHERE telegram_id=? ORDER BY id DESC LIMIT {safe}",
                    (telegram_id,),
                ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["details"] = json.loads(item.get("details_json") or "{}")
            except (TypeError, json.JSONDecodeError):
                item["details"] = {}
            result.append(item)
        return result
