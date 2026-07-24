from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from database.database import connect
from services.execution_models import CopyExecutionPlan


class JournalStatus(str, Enum):
    PLANNED = "PLANNED"
    REJECTED = "REJECTED"
    EXECUTING = "EXECUTING"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class CopyExecutionJournal:
    """Persistent idempotency boundary for future demo/live copy executors."""

    def reserve(self, plan: CopyExecutionPlan) -> tuple[dict[str, Any], bool]:
        now = datetime.now(timezone.utc).isoformat()
        status = JournalStatus.PLANNED.value if plan.approved else JournalStatus.REJECTED.value
        payload = self._plan_payload(plan)
        with connect() as conn:
            cur = conn.execute(
                """INSERT INTO copy_execution_journal(
                       idempotency_key,plan_id,telegram_id,signal_id,exchange_account_id,status,
                       code,reason,plan_json,attempt_count,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,0,?,?)
                   ON CONFLICT(idempotency_key) DO NOTHING""",
                (plan.idempotency_key, plan.plan_id, plan.telegram_id, plan.signal_id,
                 plan.exchange_account_id, status, plan.code, plan.reason,
                 json.dumps(payload, ensure_ascii=False, sort_keys=True), now, now),
            )
            created = cur.rowcount == 1
            row = conn.execute(
                "SELECT * FROM copy_execution_journal WHERE idempotency_key=?",
                (plan.idempotency_key,),
            ).fetchone()
        return dict(row), created

    def claim(self, idempotency_key: str) -> tuple[dict[str, Any], bool]:
        """Atomically claim a PLANNED execution for exactly one worker."""
        now = datetime.now(timezone.utc).isoformat()
        with connect() as conn:
            cur = conn.execute(
                """UPDATE copy_execution_journal SET status='EXECUTING',attempt_count=attempt_count+1,
                   updated_at=? WHERE idempotency_key=? AND status='PLANNED'""",
                (now, idempotency_key),
            )
            claimed = cur.rowcount == 1
            row = conn.execute(
                "SELECT * FROM copy_execution_journal WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown idempotency key: {idempotency_key}")
        return dict(row), claimed

    def transition(self, idempotency_key: str, status: JournalStatus | str, *, error: str | None = None,
                   execution_ref: str | None = None, increment_attempt: bool = False) -> dict[str, Any]:
        target = status.value if isinstance(status, JournalStatus) else JournalStatus(str(status)).value
        now = datetime.now(timezone.utc).isoformat()
        with connect() as conn:
            row = conn.execute(
                "SELECT * FROM copy_execution_journal WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown idempotency key: {idempotency_key}")
            attempts = int(dict(row).get("attempt_count") or 0) + (1 if increment_attempt else 0)
            conn.execute(
                """UPDATE copy_execution_journal SET status=?,last_error=?,execution_ref=?,
                   attempt_count=?,updated_at=? WHERE idempotency_key=?""",
                (target, error, execution_ref, attempts, now, idempotency_key),
            )
            updated = conn.execute(
                "SELECT * FROM copy_execution_journal WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
        return dict(updated)

    def get(self, idempotency_key: str) -> dict[str, Any] | None:
        with connect() as conn:
            row = conn.execute(
                "SELECT * FROM copy_execution_journal WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
        return dict(row) if row else None

    def recent(self, telegram_id: int, limit: int = 20) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 100))
        with connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM copy_execution_journal WHERE telegram_id=? ORDER BY id DESC LIMIT {safe_limit}",
                (telegram_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _plan_payload(plan: CopyExecutionPlan) -> dict[str, Any]:
        payload = asdict(plan)
        payload["status"] = plan.status.value
        return payload
