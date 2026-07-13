from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from database.database import connect


class CandidateHistory:
    def upsert(
        self,
        *,
        owner_telegram_id: int,
        notification_chat_id: int | None,
        symbol: str,
        timeframe: str,
        side: str,
        observation_id: int | None,
        blocked_by_signal_id: int | None,
        snapshot: dict[str, Any],
    ) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with connect() as conn:
            params = (
                notification_chat_id,
                observation_id,
                blocked_by_signal_id,
                "PENDING",
                json.dumps(snapshot, ensure_ascii=False, default=str),
                now,
                owner_telegram_id,
                symbol.upper(),
                timeframe,
                side,
            )
            row = conn.execute(
                """SELECT id FROM signal_candidates
                   WHERE owner_telegram_id=? AND symbol=? AND timeframe=? AND side=?""",
                (owner_telegram_id, symbol.upper(), timeframe, side),
            ).fetchone()
            if row:
                conn.execute(
                    """UPDATE signal_candidates SET notification_chat_id=?,observation_id=?,blocked_by_signal_id=?,
                       status=?,snapshot_json=?,updated_at=?,resolved_at=NULL,promoted_signal_id=NULL
                       WHERE owner_telegram_id=? AND symbol=? AND timeframe=? AND side=?""",
                    params,
                )
                return int(row[0])
            values = (
                owner_telegram_id,
                notification_chat_id,
                symbol.upper(),
                timeframe,
                side,
                observation_id,
                blocked_by_signal_id,
                "PENDING",
                json.dumps(snapshot, ensure_ascii=False, default=str),
                now,
                now,
            )
            sql = """INSERT INTO signal_candidates(
                owner_telegram_id,notification_chat_id,symbol,timeframe,side,observation_id,
                blocked_by_signal_id,status,snapshot_json,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)"""
            if conn.postgres:
                created = conn.execute(sql + " RETURNING id", values).fetchone()
                return int(created[0])
            cur = conn.execute(sql, values)
            return int(cur.lastrowid)

    def resolve_market(self, owner_telegram_id: int, symbol: str, timeframe: str, *, promoted_signal_id: int | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with connect() as conn:
            conn.execute(
                """UPDATE signal_candidates SET status=?,resolved_at=?,updated_at=?,promoted_signal_id=?
                   WHERE owner_telegram_id=? AND symbol=? AND timeframe=? AND status='PENDING'""",
                ("PROMOTED" if promoted_signal_id else "RESOLVED", now, now, promoted_signal_id,
                 owner_telegram_id, symbol.upper(), timeframe),
            )

    def recent(self, owner_telegram_id: int, limit: int = 5) -> list[dict[str, Any]]:
        with connect() as conn:
            rows = conn.execute(
                """SELECT * FROM signal_candidates
                   WHERE owner_telegram_id=? AND status='PENDING'
                   ORDER BY updated_at DESC LIMIT ?""",
                (owner_telegram_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]
