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
    RETRY_WAIT = "RETRY_WAIT"
    FAILED = "FAILED"
    DEAD_LETTER = "DEAD_LETTER"
    CANCELLED = "CANCELLED"


class InvalidJournalTransition(ValueError):
    """Raised when an execution journal lifecycle transition is not allowed."""

    def __init__(self, current: JournalStatus, target: JournalStatus) -> None:
        self.current = current
        self.target = target
        super().__init__(f"Invalid execution journal transition: {current.value} -> {target.value}")


ALLOWED_JOURNAL_TRANSITIONS: dict[JournalStatus, frozenset[JournalStatus]] = {
    JournalStatus.PLANNED: frozenset({
        JournalStatus.EXECUTING,
        JournalStatus.REJECTED,
        JournalStatus.FAILED,
        JournalStatus.CANCELLED,
    }),
    JournalStatus.EXECUTING: frozenset({
        JournalStatus.EXECUTED,
        JournalStatus.RETRY_WAIT,
        JournalStatus.FAILED,
        JournalStatus.DEAD_LETTER,
        JournalStatus.CANCELLED,
    }),
    JournalStatus.RETRY_WAIT: frozenset({
        JournalStatus.EXECUTING,
        JournalStatus.DEAD_LETTER,
        JournalStatus.CANCELLED,
    }),
    JournalStatus.REJECTED: frozenset(),
    JournalStatus.EXECUTED: frozenset(),
    JournalStatus.FAILED: frozenset(),
    JournalStatus.DEAD_LETTER: frozenset(),
    JournalStatus.CANCELLED: frozenset(),
}


def can_transition_journal_state(current: JournalStatus, target: JournalStatus) -> bool:
    """Return whether a persisted journal transition is legal.

    Same-state transitions are accepted as idempotent no-ops. Terminal states
    cannot move to another state.
    """

    if current is target:
        return True
    return target in ALLOWED_JOURNAL_TRANSITIONS[current]


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
            if created:
                self._record_event(
                    conn, plan.idempotency_key, plan.telegram_id, plan.signal_id,
                    None, status, actor="planner", reason_code=plan.code, reason=plan.reason,
                    metadata={"plan_id": plan.plan_id},
                )
        return dict(row), created

    def claim(self, idempotency_key: str, *, worker_id: str = "copy-worker", lease_seconds: int = 180) -> tuple[dict[str, Any], bool]:
        """Atomically claim due work and attach an expiring worker lease."""
        from uuid import uuid4
        from datetime import timedelta
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        expires = (now_dt + timedelta(seconds=max(30, lease_seconds))).isoformat()
        token = uuid4().hex
        with connect() as conn:
            cur = conn.execute(
                """UPDATE copy_execution_journal SET status='EXECUTING',attempt_count=attempt_count+1,
                   claimed_by=?,claim_token=?,claimed_at=?,lease_expires_at=?,updated_at=?
                   WHERE idempotency_key=? AND (status='PLANNED' OR
                   (status='RETRY_WAIT' AND (next_attempt_at IS NULL OR next_attempt_at<=?)))""",
                (worker_id, token, now, expires, now, idempotency_key, now),
            )
            claimed = cur.rowcount == 1
            row = conn.execute(
                "SELECT * FROM copy_execution_journal WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown idempotency key: {idempotency_key}")
            if claimed:
                item = dict(row)
                previous = JournalStatus.RETRY_WAIT.value if item.get("last_retry_at") else JournalStatus.PLANNED.value
                self._record_event(
                    conn, idempotency_key, int(item["telegram_id"]), int(item["signal_id"]),
                    previous, JournalStatus.EXECUTING.value, actor="worker",
                    reason_code="CLAIMED", reason="Execution claimed with lease",
                    metadata={"attempt_count": int(item.get("attempt_count") or 0), "lease_seconds": lease_seconds, "worker_id": worker_id},
                )
        return dict(row), claimed

    def schedule_retry(self, idempotency_key: str, *, error: str, code: str, next_attempt_at: str) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        row = self.transition(
            idempotency_key, JournalStatus.RETRY_WAIT, error=error, actor="recovery",
            reason_code=code, reason="Transient failure scheduled for retry",
            metadata={"next_attempt_at": next_attempt_at},
        )
        with connect() as conn:
            conn.execute(
                """UPDATE copy_execution_journal SET next_attempt_at=?,last_retry_at=?,
                   claimed_by=NULL,claim_token=NULL,claimed_at=NULL,lease_expires_at=NULL,updated_at=?
                   WHERE idempotency_key=?""",
                (next_attempt_at, now, now, idempotency_key),
            )
            updated = conn.execute("SELECT * FROM copy_execution_journal WHERE idempotency_key=?", (idempotency_key,)).fetchone()
        return dict(updated)

    def dead_letter(self, idempotency_key: str, *, error: str, code: str = "MAX_ATTEMPTS") -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        row = self.transition(idempotency_key, JournalStatus.DEAD_LETTER, error=error, actor="recovery", reason_code=code, reason=error)
        with connect() as conn:
            conn.execute(
                """UPDATE copy_execution_journal SET dead_letter_at=?,claimed_by=NULL,claim_token=NULL,
                   claimed_at=NULL,lease_expires_at=NULL,updated_at=? WHERE idempotency_key=?""",
                (now, now, idempotency_key),
            )
            updated = conn.execute("SELECT * FROM copy_execution_journal WHERE idempotency_key=?", (idempotency_key,)).fetchone()
        return dict(updated)

    def recover_expired_claims(self, *, limit: int = 100) -> dict[str, int]:
        """Move expired EXECUTING leases back to retry wait or dead letter."""
        now = datetime.now(timezone.utc).isoformat()
        recovered = dead = 0
        with connect() as conn:
            rows = conn.execute(
                f"""SELECT * FROM copy_execution_journal WHERE status='EXECUTING'
                    AND lease_expires_at IS NOT NULL AND lease_expires_at<? ORDER BY id LIMIT {max(1,min(limit,500))}""",
                (now,),
            ).fetchall()
        for raw in rows:
            item = dict(raw)
            if int(item.get("attempt_count") or 0) >= int(item.get("max_attempts") or 5):
                self.dead_letter(item["idempotency_key"], error="Worker lease expired after maximum attempts", code="LEASE_EXPIRED")
                dead += 1
            else:
                self.schedule_retry(item["idempotency_key"], error="Worker lease expired", code="LEASE_EXPIRED", next_attempt_at=now)
                recovered += 1
        return {"recovered": recovered, "dead_lettered": dead}

    def transition(self, idempotency_key: str, status: JournalStatus | str, *, error: str | None = None,
                   execution_ref: str | None = None, increment_attempt: bool = False,
                   actor: str = "engine", reason_code: str | None = None,
                   reason: str | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        target = status if isinstance(status, JournalStatus) else JournalStatus(str(status))
        now = datetime.now(timezone.utc).isoformat()
        with connect() as conn:
            row = conn.execute(
                "SELECT * FROM copy_execution_journal WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown idempotency key: {idempotency_key}")

            current_row = dict(row)
            current = JournalStatus(current_row["status"])
            if not can_transition_journal_state(current, target):
                raise InvalidJournalTransition(current, target)

            # An idempotent same-state request must not erase existing failure or
            # execution metadata when the caller does not provide replacements.
            if current is target:
                error = error if error is not None else current_row.get("last_error")
                execution_ref = (
                    execution_ref if execution_ref is not None else current_row.get("execution_ref")
                )

            attempts = int(current_row.get("attempt_count") or 0) + (1 if increment_attempt else 0)
            cur = conn.execute(
                """UPDATE copy_execution_journal SET status=?,last_error=?,execution_ref=?,
                   attempt_count=?,updated_at=? WHERE idempotency_key=? AND status=?""",
                (target.value, error, execution_ref, attempts, now, idempotency_key, current.value),
            )
            if cur.rowcount != 1:
                latest = conn.execute(
                    "SELECT status FROM copy_execution_journal WHERE idempotency_key=?",
                    (idempotency_key,),
                ).fetchone()
                latest_status = JournalStatus(dict(latest)["status"]) if latest else current
                raise InvalidJournalTransition(latest_status, target)

            updated = conn.execute(
                "SELECT * FROM copy_execution_journal WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
            if current is not target:
                item = dict(updated)
                self._record_event(
                    conn, idempotency_key, int(item["telegram_id"]), int(item["signal_id"]),
                    current.value, target.value, actor=actor,
                    reason_code=reason_code or target.value,
                    reason=reason or error or item.get("reason"), execution_ref=execution_ref,
                    metadata=metadata or {},
                )
        return dict(updated)

    def get(self, idempotency_key: str) -> dict[str, Any] | None:
        with connect() as conn:
            row = conn.execute(
                "SELECT * FROM copy_execution_journal WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
        return dict(row) if row else None

    def pending(self, limit: int = 25) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 250))
        now = datetime.now(timezone.utc).isoformat()
        with connect() as conn:
            rows = conn.execute(
                f"""SELECT * FROM copy_execution_journal
                    WHERE status='PLANNED' OR (status='RETRY_WAIT' AND (next_attempt_at IS NULL OR next_attempt_at<=?))
                    ORDER BY id ASC LIMIT {safe_limit}""",
                (now,),
            ).fetchall()
        return [dict(row) for row in rows]

    def status_counts(self, telegram_id: int) -> dict[str, int]:
        counts = {status.value: 0 for status in JournalStatus}
        with connect() as conn:
            rows = conn.execute(
                "SELECT status,COUNT(*) AS count FROM copy_execution_journal WHERE telegram_id=? GROUP BY status",
                (telegram_id,),
            ).fetchall()
        for row in rows:
            item = dict(row)
            counts[str(item["status"])] = int(item["count"] or 0)
        counts["TOTAL"] = sum(counts[status.value] for status in JournalStatus)
        return counts

    def recent(self, telegram_id: int, limit: int = 20) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 100))
        with connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM copy_execution_journal WHERE telegram_id=? ORDER BY id DESC LIMIT {safe_limit}",
                (telegram_id,),
            ).fetchall()
        return [dict(row) for row in rows]


    def get_for_user(self, telegram_id: int, reference: str) -> dict[str, Any] | None:
        reference = str(reference).strip()
        with connect() as conn:
            if reference.isdigit():
                row = conn.execute(
                    "SELECT * FROM copy_execution_journal WHERE telegram_id=? AND (id=? OR signal_id=?) ORDER BY id DESC LIMIT 1",
                    (telegram_id, int(reference), int(reference)),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM copy_execution_journal WHERE telegram_id=? AND (idempotency_key=? OR plan_id=? OR execution_ref=?) ORDER BY id DESC LIMIT 1",
                    (telegram_id, reference, reference, reference),
                ).fetchone()
        return dict(row) if row else None

    def transition_events(
        self, *, telegram_id: int, idempotency_key: str | None = None,
        limit: int = 50, statuses: tuple[str, ...] = (),
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 250))
        clauses = ["telegram_id=?"]
        params: list[Any] = [telegram_id]
        if idempotency_key:
            clauses.append("idempotency_key=?")
            params.append(idempotency_key)
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            clauses.append(f"to_status IN ({placeholders})")
            params.extend(statuses)
        with connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM execution_transition_events WHERE {' AND '.join(clauses)} ORDER BY id DESC LIMIT {safe_limit}",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _record_event(
        conn, idempotency_key: str, telegram_id: int, signal_id: int,
        from_status: str | None, to_status: str, *, actor: str,
        reason_code: str | None = None, reason: str | None = None,
        execution_ref: str | None = None, metadata: dict[str, Any] | None = None,
    ) -> None:
        conn.execute(
            """INSERT INTO execution_transition_events(
                   idempotency_key,telegram_id,signal_id,from_status,to_status,actor,
                   reason_code,reason,execution_ref,metadata_json,created_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (idempotency_key, telegram_id, signal_id, from_status, to_status, actor,
             reason_code, reason, execution_ref,
             json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
             datetime.now(timezone.utc).isoformat()),
        )

    @staticmethod
    def _plan_payload(plan: CopyExecutionPlan) -> dict[str, Any]:
        payload = asdict(plan)
        payload["status"] = plan.status.value
        return payload
