from __future__ import annotations

import json
import socket
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from database.candidate_history import CandidateHistory
from database.database import acquire_lease, connect, release_lease
from database.signal_history import OPEN_STATUSES, SignalHistory


class TradeManager:
    """Single owner of open-trade integrity for one user/market.

    The database partial unique index is the final guard. This service also
    repairs legacy/race-created duplicates and preserves the losing opposite
    scenario as a Candidate instead of silently deleting it.
    """

    PRIORITY = {"TP2": 5, "TP1": 4, "ACTIVE": 3, "TRIGGERED": 2, "WATCHING": 1}
    LIVE = {"ACTIVE", "TP1", "TP2"}

    def __init__(self):
        self.history = SignalHistory()
        self.candidates = CandidateHistory()
        self.owner_id = f"trade-manager:{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"

    @staticmethod
    def _candidate_snapshot(signal: dict[str, Any]) -> dict[str, Any]:
        try:
            features = json.loads(signal.get("features_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            features = {}
        try:
            reasons = json.loads(signal.get("reasons_json") or "[]")
        except (TypeError, json.JSONDecodeError):
            reasons = []
        analysis = {
            **features,
            "direction": signal.get("side"),
            "entry": signal.get("entry"),
            "preferred_entry_low": signal.get("preferred_entry_low"),
            "preferred_entry_high": signal.get("preferred_entry_high"),
            "stop": signal.get("stop"),
            "tp1": signal.get("tp1"),
            "tp2": signal.get("tp2"),
            "tp3": signal.get("tp3"),
            "rr": signal.get("rr"),
            "confidence": signal.get("confidence"),
            "bull_score": signal.get("bull_score"),
            "bear_score": signal.get("bear_score"),
            "recommendation": signal.get("recommendation"),
            "reasons": reasons,
        }
        return {"analysis": analysis, "legacy_signal_id": signal.get("id")}

    @classmethod
    def _keeper(cls, rows: list[dict[str, Any]]) -> dict[str, Any]:
        max_priority = max(cls.PRIORITY.get(str(row.get("status")), 0) for row in rows)
        finalists = [row for row in rows if cls.PRIORITY.get(str(row.get("status")), 0) == max_priority]
        # Keep the first genuinely activated plan. For pending plans keep the
        # oldest plan so repeated analysis updates one stable Signal ID.
        return min(
            finalists,
            key=lambda row: (
                str(row.get("activated_at") or row.get("triggered_at") or row.get("created_at") or "9999"),
                int(row.get("id") or 0),
            ),
        )

    def reconcile_market(self, owner_telegram_id: int | None, symbol: str, timeframe: str) -> dict[str, Any] | None:
        symbol = symbol.upper()
        lease_name = f"trade-integrity:{owner_telegram_id or 0}:{symbol}:{timeframe}"
        if not acquire_lease(lease_name, self.owner_id, 30):
            rows = self.history.get_open_market(owner_telegram_id, symbol, timeframe)
            return rows[0] if rows else None
        try:
            rows = self.history.get_open_market(owner_telegram_id, symbol, timeframe)
            if len(rows) <= 1:
                return rows[0] if rows else None
            keeper = self._keeper(rows)
            keeper_live = str(keeper.get("status")) in self.LIVE
            for row in rows:
                if int(row["id"]) == int(keeper["id"]):
                    continue
                opposite = str(row.get("side")) != str(keeper.get("side"))
                if opposite and keeper_live and owner_telegram_id is not None:
                    self.candidates.upsert(
                        owner_telegram_id=owner_telegram_id,
                        notification_chat_id=row.get("notification_chat_id"),
                        symbol=symbol,
                        timeframe=timeframe,
                        side=str(row.get("side") or "LONG"),
                        observation_id=None,
                        blocked_by_signal_id=int(keeper["id"]),
                        snapshot=self._candidate_snapshot(row),
                    )
                    reason = "BLOCKED_BY_ACTIVE_TRADE"
                elif opposite:
                    reason = "DIRECTION_FLIP"
                else:
                    reason = "DUPLICATE_CONSOLIDATED"
                self.history.invalidate_open(int(row["id"]), reason)
            return self.history.get_by_id(int(keeper["id"]))
        finally:
            release_lease(lease_name, self.owner_id)

    def reconcile_owner(self, owner_telegram_id: int) -> int:
        with connect() as conn:
            groups = conn.execute(
                """
                SELECT symbol,timeframe,COUNT(*) cnt
                FROM signals
                WHERE owner_telegram_id=?
                  AND status IN ('WATCHING','TRIGGERED','ACTIVE','TP1','TP2')
                GROUP BY symbol,timeframe HAVING COUNT(*) > 1
                """,
                (owner_telegram_id,),
            ).fetchall()
        for group in groups:
            self.reconcile_market(owner_telegram_id, str(group[0]), str(group[1]))
        return len(groups)

    def reconcile_all(self) -> int:
        with connect() as conn:
            groups = conn.execute(
                """
                SELECT owner_telegram_id,symbol,timeframe,COUNT(*) cnt
                FROM signals
                WHERE status IN ('WATCHING','TRIGGERED','ACTIVE','TP1','TP2')
                GROUP BY owner_telegram_id,symbol,timeframe HAVING COUNT(*) > 1
                """
            ).fetchall()
        for group in groups:
            self.reconcile_market(group[0], str(group[1]), str(group[2]))
        return len(groups)
