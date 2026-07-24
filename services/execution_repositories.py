from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from database.database import connect


@dataclass(frozen=True)
class UnifiedOpenPositionState:
    open_count: int = 0
    symbols: tuple[str, ...] = ()
    signal_ids: frozenset[int] = frozenset()
    idempotency_keys: frozenset[str] = frozenset()
    gross_notional: float = 0.0
    net_notional: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    total_commission: float = 0.0
    long_count: int = 0
    short_count: int = 0
    confirmed_heat_r: float = 0.0
    unresolved_risk_count: int = 0
    unresolved_risk_signal_ids: frozenset[int] = frozenset()
    heat_r_by_signal: tuple[tuple[int, float], ...] = ()

class ExecutionRepository:
    """Read-model repository for orders, fills and positions."""

    def order_by_idempotency(self, key: str) -> dict[str, Any] | None:
        with connect() as conn:
            row = conn.execute("SELECT * FROM paper_execution_orders WHERE idempotency_key=?", (key,)).fetchone()
        return dict(row) if row else None

    def fills_for_order(self, order_id: int) -> list[dict[str, Any]]:
        with connect() as conn:
            rows = conn.execute(
                "SELECT * FROM paper_execution_fills WHERE order_id=? ORDER BY id ASC", (order_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def position_by_idempotency(self, key: str) -> dict[str, Any] | None:
        with connect() as conn:
            row = conn.execute("SELECT * FROM paper_execution_positions WHERE idempotency_key=?", (key,)).fetchone()
        return dict(row) if row else None

    def position_for_signal(self, telegram_id: int, signal_id: int) -> dict[str, Any] | None:
        with connect() as conn:
            row = conn.execute(
                """SELECT * FROM paper_execution_positions
                   WHERE telegram_id=? AND signal_id=? ORDER BY id DESC LIMIT 1""",
                (telegram_id, signal_id),
            ).fetchone()
        return dict(row) if row else None

    def lifecycle_events(self, position_id: int) -> list[dict[str, Any]]:
        with connect() as conn:
            rows = conn.execute(
                """SELECT * FROM paper_position_lifecycle_events
                   WHERE position_id=? ORDER BY id ASC""",
                (position_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def lifecycle_integrity(self) -> dict[str, int]:
        with connect() as conn:
            duplicate_rows = conn.execute(
                """SELECT COUNT(*) FROM (
                       SELECT telegram_id,signal_id,COUNT(*) AS count
                       FROM paper_execution_positions
                       WHERE status IN ('OPEN','PARTIALLY_FILLED','PARTIALLY_CLOSED')
                       GROUP BY telegram_id,signal_id HAVING COUNT(*)>1
                   ) duplicates"""
            ).fetchone()
            invalid_open = conn.execute(
                """SELECT COUNT(*) FROM paper_execution_positions
                   WHERE status IN ('OPEN','PARTIALLY_FILLED','PARTIALLY_CLOSED')
                     AND (quantity<=0 OR initial_quantity IS NULL OR initial_quantity<=0)"""
            ).fetchone()
            closed_with_quantity = conn.execute(
                """SELECT COUNT(*) FROM paper_execution_positions
                   WHERE status='CLOSED' AND quantity>0"""
            ).fetchone()
            fraction_mismatch = conn.execute(
                """SELECT COUNT(*) FROM paper_execution_positions
                   WHERE initial_quantity>0
                     AND ABS(remaining_fraction-(quantity/initial_quantity))>0.000000001"""
            ).fetchone()
        return {
            "duplicate_open_positions": int(duplicate_rows[0] or 0),
            "positions_missing_lifecycle_metadata": int(invalid_open[0] or 0),
            "closed_with_quantity": int(closed_with_quantity[0] or 0),
            "quantity_fraction_mismatch": int(fraction_mismatch[0] or 0),
        }

    def open_positions(self, telegram_id: int) -> list[dict[str, Any]]:
        with connect() as conn:
            rows = conn.execute(
                """SELECT * FROM paper_execution_positions
                   WHERE telegram_id=? AND status IN ('OPEN','PARTIALLY_FILLED','PARTIALLY_CLOSED') ORDER BY id ASC""",
                (telegram_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def unified_open_state(self, telegram_id: int) -> UnifiedOpenPositionState:
        rows = self.open_positions(telegram_id)
        symbols: set[str] = set()
        signal_ids: set[int] = set()
        idempotency_keys: set[str] = set()
        gross = net = unrealized = realized = commission = 0.0
        longs = shorts = unresolved_risk_count = 0
        confirmed_heat = 0.0
        unresolved_signal_ids: set[int] = set()
        heat_by_signal: dict[int, float] = {}
        for row in rows:
            symbol = str(row.get("symbol") or "").strip().upper()
            if symbol:
                symbols.add(symbol)
            signal_id = row.get("signal_id")
            if signal_id is not None:
                signal_ids.add(int(signal_id))
            key = str(row.get("idempotency_key") or "").strip()
            if key:
                idempotency_keys.add(key)
            quantity = float(row.get("quantity") or 0.0)
            price = float(row.get("last_price") or row.get("average_entry") or 0.0)
            notional = quantity * price
            side = str(row.get("side") or "").strip().upper()
            direction = 1.0 if side == "LONG" else -1.0
            gross += abs(notional)
            net += direction * notional
            unrealized += float(row.get("unrealized_pnl") or 0.0)
            realized += float(row.get("realized_pnl") or 0.0)
            commission += float(row.get("total_commission") or 0.0)
            initial_quantity = float(row.get("initial_quantity") or 0.0)
            initial_risk_amount = float(row.get("initial_risk_amount") or 0.0)
            if initial_quantity > 0 and row.get("stop_loss") is not None and initial_risk_amount > 0:
                position_heat = max(0.0, min(1.0, quantity / initial_quantity))
                confirmed_heat += position_heat
                if signal_id is not None:
                    heat_by_signal[int(signal_id)] = heat_by_signal.get(int(signal_id), 0.0) + position_heat
            else:
                unresolved_risk_count += 1
                if signal_id is not None:
                    unresolved_signal_ids.add(int(signal_id))
            longs += int(side == "LONG")
            shorts += int(side == "SHORT")
        return UnifiedOpenPositionState(
            open_count=len(rows),
            symbols=tuple(sorted(symbols)),
            signal_ids=frozenset(signal_ids),
            idempotency_keys=frozenset(idempotency_keys),
            gross_notional=round(gross, 8),
            net_notional=round(net, 8),
            unrealized_pnl=round(unrealized, 8),
            realized_pnl=round(realized, 8),
            total_commission=round(commission, 8),
            long_count=longs,
            short_count=shorts,
            confirmed_heat_r=round(confirmed_heat, 8),
            unresolved_risk_count=unresolved_risk_count,
            unresolved_risk_signal_ids=frozenset(unresolved_signal_ids),
            heat_r_by_signal=tuple(sorted(heat_by_signal.items())),
        )


    def positions_for_user(self, telegram_id: int, *, include_closed: bool = True) -> list[dict[str, Any]]:
        with connect() as conn:
            if include_closed:
                rows = conn.execute("SELECT * FROM paper_execution_positions WHERE telegram_id=? ORDER BY id ASC", (telegram_id,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM paper_execution_positions WHERE telegram_id=? AND status NOT IN ('CLOSED','CANCELLED','FAILED') ORDER BY id ASC", (telegram_id,)).fetchall()
        return [dict(row) for row in rows]
