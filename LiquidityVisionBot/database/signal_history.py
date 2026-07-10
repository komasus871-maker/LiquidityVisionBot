import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from database.database import DATABASE_NAME

OPEN_STATUSES = ("WATCHING", "TRIGGERED", "ACTIVE", "TP1", "TP2")
CLOSED_STATUSES = ("TP3", "STOP", "INVALIDATED", "EXPIRED")


class SignalHistory:
    """SQLite repository for signals, lifecycle events and statistics."""

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(DATABASE_NAME, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def find_duplicate(self, owner_telegram_id: int | None, symbol: str, timeframe: str, side: str, hours: int = 24):
        threshold = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        with self._connect() as conn:
            row = conn.execute(
                """SELECT * FROM signals
                   WHERE COALESCE(owner_telegram_id, 0)=COALESCE(?, 0)
                     AND symbol=? AND timeframe=? AND side=?
                     AND status IN ('WATCHING','TRIGGERED','ACTIVE','TP1','TP2')
                     AND created_at >= ?
                   ORDER BY id DESC LIMIT 1""",
                (owner_telegram_id, symbol, timeframe, side, threshold),
            ).fetchone()
        return dict(row) if row else None

    def save(self, signal: dict[str, Any]) -> int:
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        expires_at = signal.get("expires_at") or (now_dt + timedelta(hours=72)).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO signals (
                    owner_telegram_id, notification_chat_id,
                    symbol, timeframe, side, status, created_at, updated_at, expires_at,
                    entry, preferred_entry_low, preferred_entry_high,
                    stop, tp1, tp2, tp3, rr, confidence,
                    bull_score, bear_score, recommendation, setup_key,
                    features_json, reasons_json, max_profit_pct, max_drawdown_pct,
                    next_check_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?)
                """,
                (
                    signal.get("owner_telegram_id"), signal.get("notification_chat_id"),
                    signal["symbol"], signal["timeframe"], signal["side"], signal.get("status", "WATCHING"),
                    now, now, expires_at, signal["entry"], signal.get("preferred_entry_low"),
                    signal.get("preferred_entry_high"), signal["stop"], signal["tp1"], signal["tp2"], signal["tp3"],
                    signal["rr"], signal["confidence"], signal["bull_score"], signal["bear_score"],
                    signal["recommendation"], signal["setup_key"],
                    json.dumps(signal["features"], ensure_ascii=False),
                    json.dumps(signal["reasons"], ensure_ascii=False),
                    now,
                ),
            )
            signal_id = int(cursor.lastrowid)
            self._add_event_conn(conn, signal_id, "CREATED", signal.get("entry"), {"status": signal.get("status", "WATCHING")})
            return signal_id

    def refresh_duplicate(self, signal_id: int, signal: dict[str, Any]) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """UPDATE signals SET updated_at=?, recommendation=?, confidence=?, bull_score=?, bear_score=?,
                   preferred_entry_low=?, preferred_entry_high=?, stop=?, tp1=?, tp2=?, tp3=?, rr=?,
                   features_json=?, reasons_json=?, next_check_at=? WHERE id=?""",
                (now, signal["recommendation"], signal["confidence"], signal["bull_score"], signal["bear_score"],
                 signal.get("preferred_entry_low"), signal.get("preferred_entry_high"), signal["stop"], signal["tp1"],
                 signal["tp2"], signal["tp3"], signal["rr"], json.dumps(signal["features"], ensure_ascii=False),
                 json.dumps(signal["reasons"], ensure_ascii=False), now, signal_id),
            )
            self._add_event_conn(conn, signal_id, "REFRESHED", signal.get("entry"), {})
        return signal_id

    def claim_due(self, worker_id: str, limit: int = 25, lease_seconds: int = 90) -> list[dict[str, Any]]:
        """Atomically lease due signals to one monitor instance.

        This prevents duplicate lifecycle transitions when Render briefly runs two
        instances during a deploy or a worker restarts mid-cycle.
        """
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        lock_until = (now_dt + timedelta(seconds=lease_seconds)).isoformat()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """SELECT id FROM signals
                   WHERE status IN ('WATCHING','TRIGGERED','ACTIVE','TP1','TP2')
                     AND (next_check_at IS NULL OR next_check_at <= ?)
                     AND (monitoring_lock_until IS NULL OR monitoring_lock_until < ?)
                   ORDER BY COALESCE(next_check_at, created_at) ASC
                   LIMIT ?""",
                (now, now, limit),
            ).fetchall()
            ids = [int(row["id"]) for row in rows]
            if not ids:
                conn.commit()
                return []
            placeholders = ",".join("?" for _ in ids)
            conn.execute(
                f"""UPDATE signals
                    SET monitoring_worker_id=?, monitoring_lock_until=?
                    WHERE id IN ({placeholders})""",
                (worker_id, lock_until, *ids),
            )
            claimed = conn.execute(
                f"SELECT * FROM signals WHERE id IN ({placeholders}) ORDER BY id",
                ids,
            ).fetchall()
            conn.commit()
        return [dict(row) for row in claimed]

    def release_claim(
        self,
        signal_id: int,
        worker_id: str,
        *,
        next_check_seconds: int = 60,
        error: str | None = None,
    ) -> None:
        now_dt = datetime.now(timezone.utc)
        next_check = (now_dt + timedelta(seconds=max(10, next_check_seconds))).isoformat()
        with self._connect() as conn:
            if error:
                conn.execute(
                    """UPDATE signals SET monitoring_worker_id=NULL, monitoring_lock_until=NULL,
                       last_checked_at=?, next_check_at=?, consecutive_errors=COALESCE(consecutive_errors,0)+1,
                       last_monitor_error=? WHERE id=? AND monitoring_worker_id=?""",
                    (now_dt.isoformat(), next_check, error[:1000], signal_id, worker_id),
                )
            else:
                conn.execute(
                    """UPDATE signals SET monitoring_worker_id=NULL, monitoring_lock_until=NULL,
                       last_checked_at=?, next_check_at=?, consecutive_errors=0, last_monitor_error=NULL
                       WHERE id=? AND monitoring_worker_id=?""",
                    (now_dt.isoformat(), next_check, signal_id, worker_id),
                )

    def transition_if_current(
        self,
        signal_id: int,
        expected_statuses: tuple[str, ...],
        new_status: str,
        price: float,
        details: dict[str, Any] | None = None,
        **fields: Any,
    ) -> bool:
        """Compare-and-set lifecycle transition with event creation in one transaction."""
        allowed = {
            "current_price", "max_profit_pct", "max_drawdown_pct", "tp1_hit_at", "tp2_hit_at",
            "tp3_hit_at", "stop_hit_at", "closed_at", "triggered_at", "activated_at", "invalidated_at",
            "last_notified_status",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        updates["status"] = new_status
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        placeholders = ",".join("?" for _ in expected_statuses)
        sql_set = ", ".join(f"{key}=?" for key in updates)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute("SELECT status FROM signals WHERE id=?", (signal_id,)).fetchone()
            if not current or current["status"] not in expected_statuses:
                conn.rollback()
                return False
            cursor = conn.execute(
                f"UPDATE signals SET {sql_set} WHERE id=? AND status IN ({placeholders})",
                (*updates.values(), signal_id, *expected_statuses),
            )
            if cursor.rowcount != 1:
                conn.rollback()
                return False
            event_details = {"from": current["status"], **(details or {})}
            self._add_event_conn(conn, signal_id, new_status, price, event_details)
            conn.commit()
            return True

    def get_open(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM signals WHERE status IN ('WATCHING','TRIGGERED','ACTIVE','TP1','TP2') ORDER BY created_at ASC"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_recent(self, owner_telegram_id: int | None = None, limit: int = 10) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if owner_telegram_id is None:
                rows = conn.execute("SELECT * FROM signals ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM signals WHERE owner_telegram_id=? ORDER BY id DESC LIMIT ?", (owner_telegram_id, limit)).fetchall()
        return [dict(row) for row in rows]

    def get_by_id(self, signal_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM signals WHERE id=?", (signal_id,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _add_event_conn(conn, signal_id: int, event_type: str, price: float | None, details: dict[str, Any]):
        conn.execute(
            "INSERT INTO signal_events(signal_id,event_type,price,details_json,created_at) VALUES(?,?,?,?,?)",
            (signal_id, event_type, price, json.dumps(details, ensure_ascii=False), datetime.now(timezone.utc).isoformat()),
        )

    def add_event(self, signal_id: int, event_type: str, price: float | None = None, details: dict[str, Any] | None = None):
        with self._connect() as conn:
            self._add_event_conn(conn, signal_id, event_type, price, details or {})

    def update_lifecycle(self, signal_id: int, **fields) -> None:
        allowed = {
            "status", "current_price", "max_profit_pct", "max_drawdown_pct", "tp1_hit_at", "tp2_hit_at",
            "tp3_hit_at", "stop_hit_at", "closed_at", "triggered_at", "activated_at", "invalidated_at",
            "last_notified_status", "last_checked_at", "next_check_at", "consecutive_errors", "last_monitor_error",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        sql = ", ".join(f"{key}=?" for key in updates)
        with self._connect() as conn:
            conn.execute(f"UPDATE signals SET {sql} WHERE id=?", (*updates.values(), signal_id))

    def record_monitor_run(self, worker_id: str, checked: int, succeeded: int, failed: int, duration_ms: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO monitor_runs(worker_id,checked_count,succeeded_count,failed_count,duration_ms,created_at)
                   VALUES(?,?,?,?,?,?)""",
                (worker_id, checked, succeeded, failed, duration_ms, datetime.now(timezone.utc).isoformat()),
            )

    def get_stats(self, owner_telegram_id: int | None = None) -> dict[str, Any]:
        where = ""
        params: tuple[Any, ...] = ()
        if owner_telegram_id is not None:
            where = "WHERE owner_telegram_id=?"
            params = (owner_telegram_id,)
        with self._connect() as conn:
            row = conn.execute(f"""
                SELECT COUNT(*) total,
                    SUM(status='WATCHING') watching_count,
                    SUM(status='TRIGGERED') triggered_count,
                    SUM(status IN ('ACTIVE','TP1','TP2')) active_count,
                    SUM(status IN ('TP3','STOP','INVALIDATED','EXPIRED')) closed_count,
                    SUM(tp1_hit_at IS NOT NULL) tp1_hits,
                    SUM(tp2_hit_at IS NOT NULL) tp2_hits,
                    SUM(tp3_hit_at IS NOT NULL) tp3_hits,
                    SUM(stop_hit_at IS NOT NULL) stop_hits,
                    SUM(status='INVALIDATED') invalidated_count,
                    SUM(status='EXPIRED') expired_count,
                    AVG(CASE WHEN closed_at IS NOT NULL THEN max_profit_pct END) avg_mfe,
                    AVG(CASE WHEN closed_at IS NOT NULL THEN max_drawdown_pct END) avg_mae
                FROM signals {where}
            """, params).fetchone()
        data = dict(row)
        active_population = (data.get("tp1_hits") or 0) + (data.get("stop_hits") or 0)
        data["win_rate"] = round((data.get("tp1_hits") or 0) / active_population * 100, 2) if active_population else 0
        total = data.get("total") or 0
        for key in ("tp1", "tp2", "tp3"):
            data[f"{key}_rate"] = round((data.get(f"{key}_hits") or 0) / total * 100, 2) if total else 0
        return data
