from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from database.database import connect
from services.execution_models import PortfolioState, RiskProfile
from services.execution_validator import ExecutionValidator
from services.copy_training import CopyTrainingService


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _day_start() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


class CopyTradingService:
    """Idempotent, multi-user paper execution service with a production-grade risk ledger."""

    TERMINAL_SIGNAL_STATUSES = {"TP3", "STOP", "BREAKEVEN", "INVALIDATED", "EXPIRED", "CLOSED"}
    OPEN_SIGNAL_STATUSES = {"ACTIVE", "TP1", "TP2"}

    def __init__(self) -> None:
        self.validator = ExecutionValidator()
        self.training = CopyTrainingService()

    def ensure_profile(self, telegram_id: int) -> dict[str, Any]:
        now = _now()
        with connect() as conn:
            conn.execute(
                """INSERT INTO copy_profiles(
                       telegram_id,enabled,mode,risk_pct,max_positions,max_heat_r,daily_loss_pct,
                       max_slippage_pct,paper_balance,min_confidence,max_notional_pct,symbol_cooldown_min,
                       created_at,updated_at
                   ) VALUES(?,0,'PAPER',0.5,3,2.5,2.0,0.25,10000,55,35,30,?,?)
                   ON CONFLICT(telegram_id) DO NOTHING""",
                (telegram_id, now, now),
            )
            row = conn.execute("SELECT * FROM copy_profiles WHERE telegram_id=?", (telegram_id,)).fetchone()
        return dict(row)

    def update_profile(self, telegram_id: int, **fields: Any) -> dict[str, Any]:
        allowed = {
            "enabled", "risk_pct", "max_positions", "max_heat_r", "daily_loss_pct",
            "max_slippage_pct", "paper_balance", "min_confidence", "max_notional_pct",
            "symbol_cooldown_min",
        }
        fields = {key: value for key, value in fields.items() if key in allowed}
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
            rows = conn.execute(
                "SELECT * FROM paper_positions WHERE telegram_id=? AND status IN ('OPEN','PARTIAL')",
                (telegram_id,),
            ).fetchall()
            for row in rows:
                position = dict(row)
                exit_price = float(position.get("last_price") or position["entry_price"])
                self._close_position_conn(conn, position, exit_price, "PANIC_CLOSE", now)
        return len(rows)

    def profile_stats(self, telegram_id: int) -> dict[str, Any]:
        profile = self.ensure_profile(telegram_id)
        with connect() as conn:
            row = conn.execute(
                """SELECT COUNT(*) total,
                   SUM(CASE WHEN status IN ('OPEN','PARTIAL') THEN 1 ELSE 0 END) open_count,
                   SUM(CASE WHEN status='CLOSED' THEN 1 ELSE 0 END) closed_count,
                   SUM(CASE WHEN status='REJECTED' THEN 1 ELSE 0 END) rejected_count,
                   COALESCE(SUM(CASE WHEN status='CLOSED' THEN realized_r ELSE 0 END),0) realized_r,
                   COALESCE(SUM(realized_pnl),0) realized_pnl,
                   COALESCE(AVG(CASE WHEN status='CLOSED' THEN realized_r END),0) avg_r,
                   COALESCE(SUM(CASE WHEN status='CLOSED' AND realized_r>0 THEN 1 ELSE 0 END),0) wins,
                   COALESCE(SUM(CASE WHEN status='CLOSED' AND realized_r<0 THEN 1 ELSE 0 END),0) losses
                   FROM paper_positions WHERE telegram_id=?""",
                (telegram_id,),
            ).fetchone()
            daily = conn.execute(
                """SELECT COALESCE(SUM(realized_pnl_delta),0) pnl
                   FROM execution_events
                   WHERE telegram_id=? AND created_at>=? AND event_type IN ('PARTIAL_FILLED','CLOSED')""",
                (telegram_id, _day_start()),
            ).fetchone()
        result = dict(row)
        result["daily_pnl"] = float(daily[0] or 0.0)
        result["equity"] = float(profile["paper_balance"]) + float(result.get("realized_pnl") or 0.0)
        closed = int(result.get("closed_count") or 0)
        result["win_rate"] = (float(result.get("wins") or 0) / closed * 100.0) if closed else 0.0
        top_rejection = self.rejection_summary(telegram_id, limit=1)
        result["top_rejection_code"] = top_rejection[0]["code"] if top_rejection else None
        result["top_rejection_count"] = top_rejection[0]["count"] if top_rejection else 0
        return result

    def rejection_summary(self, telegram_id: int, limit: int = 5) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 20))
        with connect() as conn:
            rows = conn.execute(
                """SELECT rejection_code, COUNT(*) count
                   FROM paper_positions
                   WHERE telegram_id=? AND status='REJECTED'
                   GROUP BY rejection_code
                   ORDER BY count DESC""",
                (telegram_id,),
            ).fetchall()
        return [
            {"code": str(row[0] or "UNKNOWN"), "count": int(row[1] or 0)}
            for row in rows[:safe_limit]
        ]

    def sync_signal(self, signal: dict[str, Any]) -> dict[str, int]:
        opened = updated = closed = rejected = skipped = 0
        status = str(signal.get("status") or "").upper()
        with connect() as conn:
            profiles = [dict(row) for row in conn.execute(
                "SELECT * FROM copy_profiles WHERE enabled=1 AND mode='PAPER'"
            ).fetchall()]
        for profile in profiles:
            telegram_id = int(profile["telegram_id"])
            existing = self._get_position(telegram_id, int(signal["id"]))
            if status in self.OPEN_SIGNAL_STATUSES and existing is None:
                result = self._open(telegram_id, profile, signal)
                opened += int(result == "OPEN")
                rejected += int(result == "REJECTED")
            elif existing and existing["status"] in {"OPEN", "PARTIAL"}:
                outcome = self._sync_existing(existing, signal)
                updated += int(outcome == "UPDATED")
                closed += int(outcome == "CLOSED")
                skipped += int(outcome == "SKIPPED")
            elif existing and existing["status"] == "REJECTED":
                outcome = self._sync_rejected(existing, signal)
                updated += int(outcome == "UPDATED")
                skipped += int(outcome == "SKIPPED")
            else:
                skipped += 1
        return {"opened": opened, "updated": updated, "closed": closed, "rejected": rejected, "skipped": skipped}

    def sync_all(self) -> dict[str, int]:
        totals = {"opened": 0, "updated": 0, "closed": 0, "rejected": 0, "skipped": 0}
        with connect() as conn:
            signals = [dict(row) for row in conn.execute(
                """SELECT * FROM signals
                   WHERE status IN ('ACTIVE','TP1','TP2','TP3','STOP','BREAKEVEN','INVALIDATED','EXPIRED')
                   ORDER BY id DESC LIMIT 500"""
            ).fetchall()]
        for signal in signals:
            result = self.sync_signal(signal)
            for key in totals:
                totals[key] += result[key]
        return totals

    def recent_events(self, telegram_id: int, limit: int = 15) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 100))
        with connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM execution_events WHERE telegram_id=? ORDER BY id DESC LIMIT {safe_limit}",
                (telegram_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _get_position(self, telegram_id: int, signal_id: int) -> dict[str, Any] | None:
        with connect() as conn:
            row = conn.execute(
                "SELECT * FROM paper_positions WHERE telegram_id=? AND signal_id=?",
                (telegram_id, signal_id),
            ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _risk_profile(profile: dict[str, Any]) -> RiskProfile:
        return RiskProfile(
            risk_pct=float(profile["risk_pct"]),
            max_positions=int(profile["max_positions"]),
            max_heat_r=float(profile["max_heat_r"]),
            daily_loss_pct=float(profile["daily_loss_pct"]),
            max_slippage_pct=float(profile["max_slippage_pct"]),
            paper_balance=float(profile["paper_balance"]),
            min_confidence=float(profile.get("min_confidence") or 55.0),
            max_notional_pct=float(profile.get("max_notional_pct") or 35.0),
            symbol_cooldown_min=int(profile.get("symbol_cooldown_min") or 30),
        )

    def _portfolio_state(self, telegram_id: int, symbol: str, cooldown_min: int) -> PortfolioState:
        cooldown_since = (datetime.now(timezone.utc) - timedelta(minutes=max(0, cooldown_min))).isoformat()
        with connect() as conn:
            open_row = conn.execute(
                """SELECT COUNT(*) c, COALESCE(SUM(initial_risk_r * remaining_fraction),0) heat
                   FROM paper_positions WHERE telegram_id=? AND status IN ('OPEN','PARTIAL')""",
                (telegram_id,),
            ).fetchone()
            symbol_open = conn.execute(
                """SELECT COUNT(*) c FROM paper_positions
                   WHERE telegram_id=? AND symbol=? AND status IN ('OPEN','PARTIAL')""",
                (telegram_id, symbol),
            ).fetchone()
            cooldown = conn.execute(
                """SELECT COUNT(*) c FROM paper_positions
                   WHERE telegram_id=? AND symbol=? AND status='CLOSED' AND closed_at>=?""",
                (telegram_id, symbol, cooldown_since),
            ).fetchone()
            daily = conn.execute(
                """SELECT COALESCE(SUM(realized_pnl_delta),0) pnl FROM execution_events
                   WHERE telegram_id=? AND created_at>=? AND event_type IN ('PARTIAL_FILLED','CLOSED')""",
                (telegram_id, _day_start()),
            ).fetchone()
        return PortfolioState(
            open_positions=int(open_row[0] or 0),
            current_heat_r=float(open_row[1] or 0.0),
            daily_realized_pnl=float(daily[0] or 0.0),
            symbol_is_open=bool(symbol_open[0]),
            symbol_in_cooldown=bool(cooldown[0]),
        )

    def _open(self, telegram_id: int, profile: dict[str, Any], signal: dict[str, Any]) -> str:
        risk_profile = self._risk_profile(profile)
        state = self._portfolio_state(telegram_id, str(signal["symbol"]), risk_profile.symbol_cooldown_min)
        stats = self.profile_stats(telegram_id)
        equity = max(0.0, float(stats["equity"]))
        training_policy = self.training.policy_for(telegram_id, signal)
        decision = self.validator.validate(
            signal=signal,
            profile=risk_profile,
            balance=equity,
            portfolio=state,
            training_policy=training_policy,
        )
        now = _now()
        if not decision.allowed or decision.size is None:
            with connect() as conn:
                conn.execute(
                    """INSERT INTO paper_positions(
                           telegram_id,signal_id,symbol,timeframe,side,status,entry_price,last_price,stop_price,
                           rejection_code,rejection_reason,last_signal_status,created_at,updated_at
                       ) VALUES(?,?,?,?,?,'REJECTED',?,?,?,?,?,?,?,?)
                       ON CONFLICT(telegram_id,signal_id) DO NOTHING""",
                    (telegram_id, signal["id"], signal["symbol"], signal["timeframe"], signal["side"],
                     float(signal.get("entry") or 0.0), float(signal.get("current_price") or signal.get("entry") or 0.0),
                     float(signal.get("stop") or 0.0), decision.code, decision.reason,
                     signal.get("status"), now, now),
                )
                self._event_conn(conn, telegram_id, signal["id"], "REJECTED", None, 0.0, {
                    "code": decision.code, "reason": decision.reason,
                    "daily_pnl": state.daily_realized_pnl, "heat_r": state.current_heat_r,
                    "training_sample_size": decision.training_sample_size,
                    "training_expectancy_r": training_policy.expectancy_r,
                })
            return "REJECTED"

        fill = float(signal.get("current_price") or signal["entry"])
        size = decision.size
        with connect() as conn:
            conn.execute(
                """INSERT INTO paper_positions(
                       telegram_id,signal_id,symbol,timeframe,side,status,entry_price,last_price,stop_price,
                       tp1,tp2,tp3,quantity,notional,risk_amount,initial_risk_r,remaining_fraction,
                       realized_r,realized_pnl,last_signal_status,opened_at,created_at,updated_at
                   ) VALUES(?,?,?,?,?,'OPEN',?,?,?,?,?,?,?,?,?,1.0,1.0,0,0,?,?,?,?)
                   ON CONFLICT(telegram_id,signal_id) DO NOTHING""",
                (telegram_id, signal["id"], signal["symbol"], signal["timeframe"], signal["side"],
                 fill, fill, signal["stop"], signal["tp1"], signal["tp2"], signal["tp3"],
                 size.quantity, size.notional, size.risk_amount, signal.get("status"), now, now, now),
            )
            self._event_conn(conn, telegram_id, signal["id"], "OPENED", fill, 0.0, {
                "quantity": size.quantity, "notional": size.notional, "risk_amount": size.risk_amount,
                "slippage_pct": decision.expected_slippage_pct, "equity_before": equity,
                "training_sample_size": decision.training_sample_size,
                "training_expectancy_r": training_policy.expectancy_r,
                "training_risk_multiplier": decision.risk_multiplier,
            })
        return "OPEN"

    def _sync_rejected(self, position: dict[str, Any], signal: dict[str, Any]) -> str:
        """Resolve the counterfactual outcome without ever creating exposure or PnL."""
        if position.get("shadow_closed_at"):
            return "SKIPPED"
        signal_status = str(signal.get("status") or "").upper()
        terminal = signal_status in self.TERMINAL_SIGNAL_STATUSES or bool(signal.get("closed_at"))
        if not terminal:
            return "SKIPPED"
        price = float(signal.get("exit_price") or signal.get("current_price") or signal.get("entry") or 0.0)
        entry = float(signal.get("entry") or position.get("entry_price") or 0.0)
        stop = float(signal.get("stop") or position.get("stop_price") or 0.0)
        side = str(signal.get("side") or position.get("side") or "").upper()
        risk = abs(entry - stop)
        shadow_r = 0.0 if risk <= 0 else (((price - entry) if side == "LONG" else (entry - price)) / risk)
        now = _now()
        result = str(signal.get("result") or signal_status)
        with connect() as conn:
            conn.execute(
                """UPDATE paper_positions SET shadow_exit_price=?,shadow_realized_r=?,shadow_result=?,
                   shadow_closed_at=?,last_signal_status=?,updated_at=? WHERE id=?""",
                (price, shadow_r, result, now, signal_status, now, position["id"]),
            )
            self._event_conn(conn, int(position["telegram_id"]), int(position["signal_id"]),
                             "REJECTION_RESOLVED", price, 0.0, {
                                 "rejection_code": position.get("rejection_code") or "UNKNOWN",
                                 "shadow_realized_r": shadow_r, "shadow_result": result,
                                 "diagnostic_only": True,
                             })
        return "UPDATED"

    def _sync_existing(self, position: dict[str, Any], signal: dict[str, Any]) -> str:
        now = _now()
        signal_status = str(signal.get("status") or "").upper()
        if signal_status == str(position.get("last_signal_status") or "").upper() and not signal.get("closed_at"):
            return "SKIPPED"
        price = float(signal.get("exit_price") or signal.get("current_price") or position["last_price"] or position["entry_price"])
        terminal = signal_status in self.TERMINAL_SIGNAL_STATUSES or bool(signal.get("closed_at"))
        if terminal:
            with connect() as conn:
                self._close_position_conn(conn, position, price, str(signal.get("result") or signal_status), now)
            return "CLOSED"

        target_remaining = 1.0
        if signal_status == "TP1":
            target_remaining = 0.5
        elif signal_status == "TP2":
            target_remaining = 0.25
        current_remaining = float(position.get("remaining_fraction") or 0.0)
        if target_remaining >= current_remaining:
            with connect() as conn:
                conn.execute(
                    "UPDATE paper_positions SET last_price=?,last_signal_status=?,updated_at=? WHERE id=?",
                    (price, signal_status, now, position["id"]),
                )
            return "UPDATED"

        closed_fraction = current_remaining - target_remaining
        trade_r = self._r_multiple(position, price)
        realized_r_delta = trade_r * closed_fraction
        realized_pnl_delta = realized_r_delta * float(position.get("risk_amount") or 0.0)
        with connect() as conn:
            conn.execute(
                """UPDATE paper_positions SET status='PARTIAL',last_price=?,remaining_fraction=?,
                   realized_r=COALESCE(realized_r,0)+?,realized_pnl=COALESCE(realized_pnl,0)+?,
                   last_signal_status=?,updated_at=? WHERE id=?""",
                (price, target_remaining, realized_r_delta, realized_pnl_delta, signal_status, now, position["id"]),
            )
            self._event_conn(conn, int(position["telegram_id"]), int(position["signal_id"]),
                             "PARTIAL_FILLED", price, realized_pnl_delta, {
                                 "signal_status": signal_status, "closed_fraction": closed_fraction,
                                 "remaining_fraction": target_remaining, "realized_r_delta": realized_r_delta,
                             })
        return "UPDATED"

    @staticmethod
    def _r_multiple(position: dict[str, Any], price: float) -> float:
        entry = float(position["entry_price"])
        stop = float(position["stop_price"])
        side = str(position["side"]).upper()
        risk = abs(entry - stop)
        if risk <= 0:
            return 0.0
        return ((price - entry) if side == "LONG" else (entry - price)) / risk

    def _close_position_conn(self, conn, position: dict[str, Any], price: float, reason: str, now: str) -> None:
        remaining = float(position.get("remaining_fraction") or 0.0)
        trade_r = self._r_multiple(position, price)
        realized_r_delta = trade_r * remaining
        realized_pnl_delta = realized_r_delta * float(position.get("risk_amount") or 0.0)
        total_r = float(position.get("realized_r") or 0.0) + realized_r_delta
        total_pnl = float(position.get("realized_pnl") or 0.0) + realized_pnl_delta
        conn.execute(
            """UPDATE paper_positions SET status='CLOSED',last_price=?,exit_price=?,remaining_fraction=0,
               realized_r=?,realized_pnl=?,close_reason=?,last_signal_status=?,closed_at=?,updated_at=? WHERE id=?""",
            (price, price, total_r, total_pnl, reason, reason, now, now, position["id"]),
        )
        self._event_conn(conn, int(position["telegram_id"]), int(position["signal_id"]),
                         "CLOSED", price, realized_pnl_delta, {
                             "reason": reason, "remaining_fraction": remaining,
                             "realized_r_delta": realized_r_delta, "total_realized_r": total_r,
                             "total_realized_pnl": total_pnl,
                         })

    @staticmethod
    def _event_conn(
        conn,
        telegram_id: int,
        signal_id: int,
        event_type: str,
        price: float | None,
        realized_pnl_delta: float,
        details: dict[str, Any],
    ) -> None:
        conn.execute(
            """INSERT INTO execution_events(
                   telegram_id,signal_id,event_type,price,realized_pnl_delta,details_json,created_at
               ) VALUES(?,?,?,?,?,?,?)""",
            (telegram_id, signal_id, event_type, price, realized_pnl_delta,
             json.dumps(details, ensure_ascii=False), _now()),
        )
