from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from database.database import connect

OPEN_STATUSES = ("WATCHING", "TRIGGERED", "ACTIVE", "TP1", "TP2")
CLOSED_STATUSES = ("TP3", "STOP", "BREAKEVEN", "INVALIDATED", "EXPIRED")


class SignalHistory:
    """Repository for signals, lifecycle events and statistics."""

    def _connect(self):
        return connect()

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
            params = (
                signal.get("owner_telegram_id"), signal.get("notification_chat_id"), signal["symbol"],
                signal["timeframe"], signal["side"], signal.get("status", "WATCHING"), now, now, expires_at,
                signal["entry"], signal.get("preferred_entry_low"), signal.get("preferred_entry_high"),
                signal["stop"], signal["tp1"], signal["tp2"], signal["tp3"], signal["rr"],
                signal["confidence"], signal["bull_score"], signal["bear_score"], signal["recommendation"],
                signal["setup_key"], json.dumps(signal["features"], ensure_ascii=False),
                json.dumps(signal["reasons"], ensure_ascii=False),
            )
            if conn.postgres:
                row = conn.execute(
                    """
                    INSERT INTO signals (
                        owner_telegram_id, notification_chat_id, symbol, timeframe, side, status,
                        created_at, updated_at, expires_at, entry, preferred_entry_low, preferred_entry_high,
                        stop, tp1, tp2, tp3, rr, confidence, bull_score, bear_score, recommendation,
                        setup_key, features_json, reasons_json, max_profit_pct, max_drawdown_pct
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,0) RETURNING id
                    """,
                    params,
                ).fetchone()
                signal_id = int(row[0])
            else:
                cur = conn.execute(
                    """
                    INSERT INTO signals (
                        owner_telegram_id, notification_chat_id, symbol, timeframe, side, status,
                        created_at, updated_at, expires_at, entry, preferred_entry_low, preferred_entry_high,
                        stop, tp1, tp2, tp3, rr, confidence, bull_score, bear_score, recommendation,
                        setup_key, features_json, reasons_json, max_profit_pct, max_drawdown_pct
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,0)
                    """,
                    params,
                )
                signal_id = int(cur.lastrowid)
            self._add_event_conn(conn, signal_id, "CREATED", signal.get("entry"), {"status": signal.get("status", "WATCHING")})
            return signal_id

    def refresh_duplicate(self, signal_id: int, signal: dict[str, Any]) -> int:
        now = datetime.now(timezone.utc).isoformat()
        desired_status = signal.get("status", "WATCHING")
        activated_at = now if desired_status == "ACTIVE" else None
        effective_stop = float(signal["stop"]) if desired_status == "ACTIVE" else None
        with self._connect() as conn:
            conn.execute(
                """UPDATE signals SET updated_at=?, status=?, recommendation=?, confidence=?, bull_score=?, bear_score=?,
                   entry=?, preferred_entry_low=?, preferred_entry_high=?, stop=?, effective_stop=COALESCE(?,effective_stop),
                   tp1=?, tp2=?, tp3=?, rr=?, activated_at=COALESCE(?,activated_at),
                   current_price=CASE WHEN ?='ACTIVE' THEN ? ELSE current_price END,
                   highest_price=CASE WHEN ?='ACTIVE' THEN COALESCE(highest_price,?) ELSE highest_price END,
                   lowest_price=CASE WHEN ?='ACTIVE' THEN COALESCE(lowest_price,?) ELSE lowest_price END,
                   features_json=?, reasons_json=? WHERE id=?""",
                (now, desired_status, signal["recommendation"], signal["confidence"], signal["bull_score"], signal["bear_score"],
                 signal["entry"], signal.get("preferred_entry_low"), signal.get("preferred_entry_high"), signal["stop"], effective_stop,
                 signal["tp1"], signal["tp2"], signal["tp3"], signal["rr"], activated_at,
                 desired_status, signal["entry"], desired_status, signal["entry"], desired_status, signal["entry"],
                 json.dumps(signal["features"], ensure_ascii=False), json.dumps(signal["reasons"], ensure_ascii=False), signal_id),
            )
            self._add_event_conn(conn, signal_id, "REFRESHED", signal.get("entry"), {"status": desired_status})
            if desired_status == "ACTIVE":
                self._add_event_conn(conn, signal_id, "ACTIVE", signal.get("entry"), {"from": "PROMOTION"})
        return signal_id

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
            "last_notified_status", "effective_stop", "break_even_at", "exit_price",
            "realized_r", "result", "highest_price", "lowest_price",
            "last_progress_notified_at", "last_progress_bucket",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        sql = ", ".join(f"{key}=?" for key in updates)
        with self._connect() as conn:
            conn.execute(f"UPDATE signals SET {sql} WHERE id=?", (*updates.values(), signal_id))

    def get_events(self, signal_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM signal_events WHERE signal_id=? ORDER BY id ASC",
                (signal_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def close_opposite_pending(self, owner_telegram_id: int | None, symbol: str, timeframe: str, side: str) -> int:
        """Invalidate stale opposite pre-entry ideas before promoting a new direction."""
        opposite = "SHORT" if side == "LONG" else "LONG"
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT id,current_price,entry FROM signals
                   WHERE COALESCE(owner_telegram_id,0)=COALESCE(?,0)
                     AND symbol=? AND timeframe=? AND side=?
                     AND status IN ('WATCHING','TRIGGERED')""",
                (owner_telegram_id, symbol, timeframe, opposite),
            ).fetchall()
            for row in rows:
                signal_id = int(row[0])
                price = row[1] if row[1] is not None else row[2]
                conn.execute(
                    "UPDATE signals SET status='INVALIDATED', invalidated_at=?, closed_at=?, updated_at=?, result='DIRECTION_FLIP' WHERE id=?",
                    (now, now, now, signal_id),
                )
                self._add_event_conn(conn, signal_id, "INVALIDATED", price, {"reason": "direction_flip"})
        return len(rows)

    def get_stats(self, owner_telegram_id: int | None = None) -> dict[str, Any]:
        where = ""
        params: tuple[Any, ...] = ()
        if owner_telegram_id is not None:
            where = "WHERE owner_telegram_id=?"
            params = (owner_telegram_id,)
        with self._connect() as conn:
            row = conn.execute(f"""
                SELECT COUNT(*) total,
                    SUM(CASE WHEN status='WATCHING' THEN 1 ELSE 0 END) watching_count,
                    SUM(CASE WHEN status='TRIGGERED' THEN 1 ELSE 0 END) triggered_count,
                    SUM(CASE WHEN status IN ('ACTIVE','TP1','TP2') THEN 1 ELSE 0 END) active_count,
                    SUM(CASE WHEN status IN ('TP3','STOP','BREAKEVEN','INVALIDATED','EXPIRED') THEN 1 ELSE 0 END) closed_count,
                    SUM(CASE WHEN tp1_hit_at IS NOT NULL THEN 1 ELSE 0 END) tp1_hits,
                    SUM(CASE WHEN tp2_hit_at IS NOT NULL THEN 1 ELSE 0 END) tp2_hits,
                    SUM(CASE WHEN tp3_hit_at IS NOT NULL THEN 1 ELSE 0 END) tp3_hits,
                    SUM(CASE WHEN status='STOP' THEN 1 ELSE 0 END) stop_hits,
                    SUM(CASE WHEN status='BREAKEVEN' THEN 1 ELSE 0 END) breakeven_count,
                    SUM(CASE WHEN status='INVALIDATED' THEN 1 ELSE 0 END) invalidated_count,
                    SUM(CASE WHEN status='EXPIRED' THEN 1 ELSE 0 END) expired_count,
                    AVG(CASE WHEN closed_at IS NOT NULL THEN max_profit_pct END) avg_mfe,
                    AVG(CASE WHEN closed_at IS NOT NULL THEN max_drawdown_pct END) avg_mae,
                    AVG(CASE WHEN closed_at IS NOT NULL THEN realized_r END) avg_realized_r
                FROM signals {where}
            """, params).fetchone()
        data = dict(row)
        active_population = (data.get("tp1_hits") or 0) + (data.get("stop_hits") or 0)
        data["win_rate"] = round((data.get("tp1_hits") or 0) / active_population * 100, 2) if active_population else 0
        total = data.get("total") or 0
        for key in ("tp1", "tp2", "tp3"):
            data[f"{key}_rate"] = round((data.get(f"{key}_hits") or 0) / total * 100, 2) if total else 0
        return data
