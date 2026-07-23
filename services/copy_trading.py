from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from database.database import connect
from services.execution_models import RiskProfile
from services.execution_validator import ExecutionValidator


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CopyTradingService:
    """Multi-user paper-copy service. LIVE mode remains hard-disabled in v9.0."""

    def __init__(self) -> None:
        self.validator = ExecutionValidator()

    def ensure_profile(self, telegram_id: int) -> dict[str, Any]:
        now = _now()
        with connect() as conn:
            conn.execute(
                """INSERT INTO copy_profiles(telegram_id,enabled,mode,risk_pct,max_positions,max_heat_r,daily_loss_pct,max_slippage_pct,paper_balance,created_at,updated_at)
                   VALUES(?,0,'PAPER',0.5,3,2.5,2.0,0.25,10000,?,?) ON CONFLICT(telegram_id) DO NOTHING""",
                (telegram_id, now, now),
            )
            row = conn.execute("SELECT * FROM copy_profiles WHERE telegram_id=?", (telegram_id,)).fetchone()
        return dict(row)

    def update_profile(self, telegram_id: int, **fields: Any) -> dict[str, Any]:
        allowed = {"enabled", "risk_pct", "max_positions", "max_heat_r", "daily_loss_pct", "max_slippage_pct", "paper_balance"}
        fields = {k: v for k, v in fields.items() if k in allowed}
        self.ensure_profile(telegram_id)
        if fields:
            fields["updated_at"] = _now()
            assignments = ",".join(f"{key}=?" for key in fields)
            with connect() as conn:
                conn.execute(f"UPDATE copy_profiles SET {assignments} WHERE telegram_id=?", (*fields.values(), telegram_id))
        return self.ensure_profile(telegram_id)

    def panic(self, telegram_id: int) -> int:
        now = _now()
        with connect() as conn:
            conn.execute("UPDATE copy_profiles SET enabled=0,updated_at=? WHERE telegram_id=?", (now, telegram_id))
            rows = conn.execute("SELECT * FROM paper_positions WHERE telegram_id=? AND status IN ('OPEN','PARTIAL')", (telegram_id,)).fetchall()
            for row in rows:
                position = dict(row)
                exit_price = float(position.get("last_price") or position["entry_price"])
                self._close_position_conn(conn, position, exit_price, "PANIC_CLOSE", now)
        return len(rows)

    def profile_stats(self, telegram_id: int) -> dict[str, Any]:
        self.ensure_profile(telegram_id)
        with connect() as conn:
            row = conn.execute(
                """SELECT COUNT(*) total,
                   SUM(CASE WHEN status IN ('OPEN','PARTIAL') THEN 1 ELSE 0 END) open_count,
                   SUM(CASE WHEN status='CLOSED' THEN 1 ELSE 0 END) closed_count,
                   SUM(CASE WHEN status='REJECTED' THEN 1 ELSE 0 END) rejected_count,
                   COALESCE(SUM(CASE WHEN status='CLOSED' THEN realized_r ELSE 0 END),0) realized_r,
                   COALESCE(AVG(CASE WHEN status='CLOSED' THEN realized_r END),0) avg_r
                   FROM paper_positions WHERE telegram_id=?""",
                (telegram_id,),
            ).fetchone()
        return dict(row)

    def sync_signal(self, signal: dict[str, Any]) -> dict[str, int]:
        opened = updated = closed = rejected = 0
        status = str(signal.get("status"))
        with connect() as conn:
            profiles = [dict(r) for r in conn.execute("SELECT * FROM copy_profiles WHERE enabled=1 AND mode='PAPER'").fetchall()]
        for profile in profiles:
            telegram_id = int(profile["telegram_id"])
            existing = self._get_position(telegram_id, int(signal["id"]))
            if status in {"ACTIVE", "TP1", "TP2"} and existing is None:
                result = self._open(telegram_id, profile, signal)
                opened += int(result == "OPEN")
                rejected += int(result == "REJECTED")
            elif existing and existing["status"] in {"OPEN", "PARTIAL"}:
                outcome = self._sync_existing(existing, signal)
                updated += int(outcome == "UPDATED")
                closed += int(outcome == "CLOSED")
        return {"opened": opened, "updated": updated, "closed": closed, "rejected": rejected}

    def sync_all(self) -> dict[str, int]:
        totals = {"opened": 0, "updated": 0, "closed": 0, "rejected": 0}
        with connect() as conn:
            signals = [dict(r) for r in conn.execute("SELECT * FROM signals WHERE status IN ('ACTIVE','TP1','TP2','TP3','STOP','BREAKEVEN','INVALIDATED','EXPIRED') ORDER BY id DESC LIMIT 500").fetchall()]
        for signal in signals:
            result = self.sync_signal(signal)
            for key in totals:
                totals[key] += result[key]
        return totals

    def _get_position(self, telegram_id: int, signal_id: int) -> dict[str, Any] | None:
        with connect() as conn:
            row = conn.execute("SELECT * FROM paper_positions WHERE telegram_id=? AND signal_id=?", (telegram_id, signal_id)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _risk_profile(profile: dict[str, Any]) -> RiskProfile:
        return RiskProfile(
            risk_pct=float(profile["risk_pct"]), max_positions=int(profile["max_positions"]),
            max_heat_r=float(profile["max_heat_r"]), daily_loss_pct=float(profile["daily_loss_pct"]),
            max_slippage_pct=float(profile["max_slippage_pct"]), paper_balance=float(profile["paper_balance"]),
        )

    def _open(self, telegram_id: int, profile: dict[str, Any], signal: dict[str, Any]) -> str:
        with connect() as conn:
            open_count = int(conn.execute("SELECT COUNT(*) c FROM paper_positions WHERE telegram_id=? AND status IN ('OPEN','PARTIAL')", (telegram_id,)).fetchone()[0])
            heat = float(conn.execute("SELECT COALESCE(SUM(initial_risk_r),0) h FROM paper_positions WHERE telegram_id=? AND status IN ('OPEN','PARTIAL')", (telegram_id,)).fetchone()[0])
        decision = self.validator.validate(signal=signal, profile=self._risk_profile(profile), balance=float(profile["paper_balance"]), open_positions=open_count, current_heat_r=heat)
        now = _now()
        if not decision.allowed or decision.size is None:
            with connect() as conn:
                conn.execute(
                    """INSERT INTO paper_positions(telegram_id,signal_id,symbol,timeframe,side,status,rejection_code,rejection_reason,created_at,updated_at)
                       VALUES(?,?,?,?,?,'REJECTED',?,?,?,?) ON CONFLICT(telegram_id,signal_id) DO NOTHING""",
                    (telegram_id, signal["id"], signal["symbol"], signal["timeframe"], signal["side"], decision.code, decision.reason, now, now),
                )
                self._event_conn(conn, telegram_id, signal["id"], "REJECTED", None, {"code": decision.code, "reason": decision.reason})
            return "REJECTED"
        fill = float(signal.get("current_price") or signal["entry"])
        size = decision.size
        with connect() as conn:
            conn.execute(
                """INSERT INTO paper_positions(telegram_id,signal_id,symbol,timeframe,side,status,entry_price,last_price,stop_price,tp1,tp2,tp3,quantity,notional,risk_amount,initial_risk_r,remaining_fraction,opened_at,created_at,updated_at)
                   VALUES(?,?,?,?,?,'OPEN',?,?,?,?,?,?,?,?,?,1.0,1.0,?,?,?) ON CONFLICT(telegram_id,signal_id) DO NOTHING""",
                (telegram_id, signal["id"], signal["symbol"], signal["timeframe"], signal["side"], fill, fill,
                 signal["stop"], signal["tp1"], signal["tp2"], signal["tp3"], size.quantity, size.notional,
                 size.risk_amount, now, now, now),
            )
            self._event_conn(conn, telegram_id, signal["id"], "OPENED", fill, {"quantity": size.quantity, "notional": size.notional, "risk_amount": size.risk_amount})
        return "OPEN"

    def _sync_existing(self, position: dict[str, Any], signal: dict[str, Any]) -> str:
        now = _now(); price = float(signal.get("exit_price") or signal.get("current_price") or position["last_price"] or position["entry_price"])
        terminal = str(signal.get("status")) in {"TP3", "STOP", "BREAKEVEN", "INVALIDATED", "EXPIRED"} or signal.get("closed_at")
        if terminal:
            with connect() as conn:
                self._close_position_conn(conn, position, price, str(signal.get("result") or signal.get("status")), now)
            return "CLOSED"
        remaining = 1.0
        if str(signal.get("status")) == "TP1": remaining = 0.5
        elif str(signal.get("status")) == "TP2": remaining = 0.25
        with connect() as conn:
            conn.execute("UPDATE paper_positions SET status=?,last_price=?,remaining_fraction=?,updated_at=? WHERE id=?", ("PARTIAL" if remaining < 1 else "OPEN", price, remaining, now, position["id"]))
        return "UPDATED"

    @staticmethod
    def _r_multiple(position: dict[str, Any], price: float) -> float:
        entry = float(position["entry_price"]); stop = float(position["stop_price"]); side = str(position["side"])
        risk = abs(entry - stop)
        if risk <= 0: return 0.0
        return ((price - entry) if side == "LONG" else (entry - price)) / risk

    def _close_position_conn(self, conn, position: dict[str, Any], price: float, reason: str, now: str) -> None:
        realized_r = self._r_multiple(position, price)
        conn.execute("UPDATE paper_positions SET status='CLOSED',last_price=?,exit_price=?,realized_r=?,close_reason=?,closed_at=?,updated_at=? WHERE id=?", (price, price, realized_r, reason, now, now, position["id"]))
        self._event_conn(conn, int(position["telegram_id"]), int(position["signal_id"]), "CLOSED", price, {"reason": reason, "realized_r": realized_r})

    @staticmethod
    def _event_conn(conn, telegram_id: int, signal_id: int, event_type: str, price: float | None, details: dict[str, Any]) -> None:
        conn.execute("INSERT INTO execution_events(telegram_id,signal_id,event_type,price,details_json,created_at) VALUES(?,?,?,?,?,?)", (telegram_id, signal_id, event_type, price, json.dumps(details, ensure_ascii=False), _now()))
