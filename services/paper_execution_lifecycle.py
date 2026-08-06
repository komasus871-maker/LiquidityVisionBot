from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from database.database import connect
from services.execution_context import ExecutionContext
from services.execution_models import CopyExecutionPlan


class PositionStatus(str, Enum):
    CREATED = "CREATED"
    OPENING = "OPENING"
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    PARTIALLY_CLOSED = "PARTIALLY_CLOSED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class OrderStatus(str, Enum):
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


TERMINAL_ORDER_STATUSES = {
    OrderStatus.FILLED,
    OrderStatus.CANCELLED,
    OrderStatus.REJECTED,
    OrderStatus.EXPIRED,
    OrderStatus.FAILED,
}

ALLOWED_ORDER_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.SUBMITTED: frozenset({OrderStatus.ACCEPTED, OrderStatus.REJECTED, OrderStatus.FAILED, OrderStatus.CANCELLED}),
    OrderStatus.ACCEPTED: frozenset({OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.EXPIRED, OrderStatus.FAILED}),
    OrderStatus.OPEN: frozenset({OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.EXPIRED, OrderStatus.FAILED}),
    OrderStatus.PARTIALLY_FILLED: frozenset({OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.EXPIRED, OrderStatus.FAILED}),
    OrderStatus.FILLED: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
    OrderStatus.REJECTED: frozenset(),
    OrderStatus.EXPIRED: frozenset(),
    OrderStatus.FAILED: frozenset(),
}


class InvalidOrderTransition(ValueError):
    def __init__(self, current: OrderStatus, target: OrderStatus) -> None:
        self.current = current
        self.target = target
        super().__init__(f"Invalid paper order transition: {current.value} -> {target.value}")


def can_transition_order(current: OrderStatus, target: OrderStatus) -> bool:
    return current is target or target in ALLOWED_ORDER_TRANSITIONS[current]


@dataclass(frozen=True)
class FillResult:
    order: dict[str, Any]
    fill: dict[str, Any] | None
    position: dict[str, Any] | None
    created: bool


@dataclass(frozen=True)
class PositionLifecycleResult:
    position: dict[str, Any]
    applied: bool
    event_type: str
    realized_pnl_delta: float = 0.0
    realized_r_delta: float = 0.0
    event_key: str | None = None


class PaperExecutionLifecycle:
    """Persistent execution → order → fill → position lifecycle.

    The service is deliberately independent from exchange adapters. It is safe to
    call repeatedly: unique execution/idempotency keys and fill keys prevent
    duplicate economic effects after retries or worker restarts.
    """

    DEFAULT_COMMISSION_RATE = 0.0005
    TERMINAL_SIGNAL_STATUSES = {
        "TP3", "STOP", "BREAKEVEN", "MANUAL_STOP", "INVALIDATED",
        "EXPIRED", "CLOSED", "PANIC",
    }

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def submit_context(self, context: ExecutionContext, *, execution_ref: str | None = None) -> tuple[ExecutionContext, bool]:
        order, created = self.submit(context.plan, execution_ref=execution_ref)
        return context.with_order(order), created

    def reject_context(self, context: ExecutionContext, *, reason_code: str, reason: str) -> ExecutionContext:
        order = self.reject(context.plan, reason_code=reason_code, reason=reason)
        return context.with_order(order).merge_metadata(pipeline_stage="REJECTED", rejection_code=reason_code)

    def execute_market_context(
        self, context: ExecutionContext, *, fill_price: float, execution_ref: str,
        commission_rate: float = DEFAULT_COMMISSION_RATE, slippage_pct: float | None = None,
    ) -> ExecutionContext:
        result = self.execute_market(
            context.plan, fill_price=fill_price, execution_ref=execution_ref,
            commission_rate=commission_rate, slippage_pct=slippage_pct,
        )
        updated = context.with_order(result.order).add_fill(result.fill).with_position(result.position)
        stage = "POSITIONED" if result.position else ("FILLED" if result.fill else "ORDERED")
        return updated.merge_metadata(pipeline_stage=stage, lifecycle_created=result.created)

    def submit(self, plan: CopyExecutionPlan, *, execution_ref: str | None = None) -> tuple[dict[str, Any], bool]:
        now = self._now()
        requested_qty = float(plan.quantity or 0.0)
        with connect() as conn:
            cur = conn.execute(
                """INSERT INTO paper_execution_orders(
                       order_key,idempotency_key,plan_id,telegram_id,signal_id,execution_ref,
                       symbol,timeframe,side,order_type,status,requested_quantity,filled_quantity,
                       average_fill_price,limit_price,notional,leverage,stop_loss,risk_amount,
                       last_error,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?, ?,0,NULL,?,?,?,?,?,NULL,?,?)
                   ON CONFLICT(order_key) DO NOTHING""",
                (plan.idempotency_key, plan.idempotency_key, plan.plan_id, plan.telegram_id,
                 plan.signal_id, execution_ref, plan.symbol, plan.timeframe, plan.side,
                 plan.order_type, OrderStatus.SUBMITTED.value, requested_qty,
                 plan.entry_price, plan.notional, plan.leverage, plan.stop_loss,
                 plan.risk_amount, now, now),
            )
            created = cur.rowcount == 1
            row = conn.execute(
                "SELECT * FROM paper_execution_orders WHERE order_key=?", (plan.idempotency_key,)
            ).fetchone()
            if created:
                self._event(conn, int(dict(row)["id"]), None, OrderStatus.SUBMITTED, "engine", "ORDER_SUBMITTED")
        return dict(row), created

    def reject(self, plan: CopyExecutionPlan, *, reason_code: str, reason: str) -> dict[str, Any]:
        row, _ = self.submit(plan)
        current = OrderStatus(str(row["status"]))
        if current is OrderStatus.REJECTED:
            return row
        return self.transition(int(row["id"]), OrderStatus.REJECTED, actor="planner", reason_code=reason_code, reason=reason)

    def transition(
        self, order_id: int, target: OrderStatus | str, *, actor: str = "engine",
        reason_code: str | None = None, reason: str | None = None,
        execution_ref: str | None = None,
    ) -> dict[str, Any]:
        target_status = target if isinstance(target, OrderStatus) else OrderStatus(str(target))
        now = self._now()
        with connect() as conn:
            row = conn.execute("SELECT * FROM paper_execution_orders WHERE id=?", (order_id,)).fetchone()
            if row is None:
                raise KeyError(f"Unknown paper order: {order_id}")
            item = dict(row)
            current = OrderStatus(str(item["status"]))
            if not can_transition_order(current, target_status):
                raise InvalidOrderTransition(current, target_status)
            if current is target_status:
                return item
            cur = conn.execute(
                """UPDATE paper_execution_orders SET status=?,execution_ref=COALESCE(?,execution_ref),
                   last_error=?,updated_at=? WHERE id=? AND status=?""",
                (target_status.value, execution_ref, reason if target_status in {OrderStatus.FAILED, OrderStatus.REJECTED} else item.get("last_error"),
                 now, order_id, current.value),
            )
            if cur.rowcount != 1:
                raise InvalidOrderTransition(current, target_status)
            self._event(conn, order_id, current, target_status, actor, reason_code or target_status.value, reason)
            updated = conn.execute("SELECT * FROM paper_execution_orders WHERE id=?", (order_id,)).fetchone()
        return dict(updated)

    def execute_market(
        self, plan: CopyExecutionPlan, *, fill_price: float, execution_ref: str,
        commission_rate: float = DEFAULT_COMMISSION_RATE,
        slippage_pct: float | None = None,
    ) -> FillResult:
        order, created = self.submit(plan, execution_ref=execution_ref)
        order_id = int(order["id"])
        status = OrderStatus(str(order["status"]))
        if status in {OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED}:
            fill = self._latest_fill(order_id)
            position = self.position_for_order(order_id)
            return FillResult(order=order, fill=fill, position=position, created=False)
        if status is OrderStatus.SUBMITTED:
            order = self.transition(order_id, OrderStatus.ACCEPTED, actor="paper_adapter", reason_code="PAPER_ACCEPTED", execution_ref=execution_ref)
        quantity = float(plan.quantity or 0.0)
        if quantity <= 0:
            failed = self.transition(order_id, OrderStatus.FAILED, actor="paper_adapter", reason_code="INVALID_QUANTITY", reason="Quantity must be positive")
            return FillResult(order=failed, fill=None, position=None, created=created)
        return self.record_fill(
            order_id, quantity=quantity, price=float(fill_price),
            commission_rate=float(commission_rate), slippage_pct=float(slippage_pct if slippage_pct is not None else plan.expected_slippage_pct),
            fill_key=f"{plan.idempotency_key}:full", actor="paper_adapter",
        )

    def record_fill(
        self, order_id: int, *, quantity: float, price: float,
        commission_rate: float = DEFAULT_COMMISSION_RATE, slippage_pct: float = 0.0,
        fill_key: str | None = None, actor: str = "paper_adapter",
    ) -> FillResult:
        if quantity <= 0 or price <= 0:
            raise ValueError("Fill quantity and price must be positive")
        fill_key = fill_key or f"fill:{order_id}:{uuid4().hex}"
        now = self._now()
        with connect() as conn:
            order_row = conn.execute("SELECT * FROM paper_execution_orders WHERE id=?", (order_id,)).fetchone()
            if order_row is None:
                raise KeyError(f"Unknown paper order: {order_id}")
            order = dict(order_row)
            current = OrderStatus(str(order["status"]))
            if current in TERMINAL_ORDER_STATUSES and current is not OrderStatus.FILLED:
                raise InvalidOrderTransition(current, OrderStatus.PARTIALLY_FILLED)
            existing = conn.execute("SELECT * FROM paper_execution_fills WHERE fill_key=?", (fill_key,)).fetchone()
            if existing is not None:
                return FillResult(order=order, fill=dict(existing), position=self._position_for_order_conn(conn, order_id), created=False)

            requested = float(order.get("requested_quantity") or 0.0)
            previous_qty = float(order.get("filled_quantity") or 0.0)
            remaining = max(0.0, requested - previous_qty)
            applied_qty = min(float(quantity), remaining)
            if applied_qty <= 0:
                return FillResult(order=order, fill=self._latest_fill_conn(conn, order_id), position=self._position_for_order_conn(conn, order_id), created=False)
            previous_avg = float(order.get("average_fill_price") or 0.0)
            new_qty = previous_qty + applied_qty
            average_price = ((previous_avg * previous_qty) + (price * applied_qty)) / new_qty
            notional = applied_qty * price
            commission = notional * max(0.0, commission_rate)
            cur = conn.execute(
                """INSERT INTO paper_execution_fills(
                       fill_key,order_id,idempotency_key,telegram_id,signal_id,execution_ref,
                       quantity,price,notional,commission,commission_rate,slippage_pct,liquidity_type,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(fill_key) DO NOTHING""",
                (fill_key, order_id, order["idempotency_key"], order["telegram_id"], order["signal_id"],
                 order.get("execution_ref"), applied_qty, price, notional, commission,
                 commission_rate, slippage_pct, "TAKER", now),
            )
            if cur.rowcount != 1:
                existing = conn.execute("SELECT * FROM paper_execution_fills WHERE fill_key=?", (fill_key,)).fetchone()
                return FillResult(order=order, fill=dict(existing), position=self._position_for_order_conn(conn, order_id), created=False)
            fill = dict(conn.execute("SELECT * FROM paper_execution_fills WHERE fill_key=?", (fill_key,)).fetchone())
            target = OrderStatus.FILLED if requested <= 0 or new_qty >= requested - 1e-12 else OrderStatus.PARTIALLY_FILLED
            if not can_transition_order(current, target):
                raise InvalidOrderTransition(current, target)
            conn.execute(
                """UPDATE paper_execution_orders SET status=?,filled_quantity=?,average_fill_price=?,
                   updated_at=? WHERE id=?""",
                (target.value, new_qty, average_price, now, order_id),
            )
            self._event(conn, order_id, current, target, actor, "FULL_FILL" if target is OrderStatus.FILLED else "PARTIAL_FILL",
                        f"Filled {applied_qty:g} at {price:g}")
            position = self._upsert_position(conn, order, fill, new_qty, average_price, commission, now)
            conn.execute(
                """INSERT INTO paper_portfolio_ledger(
                       source_key,telegram_id,position_id,order_id,entry_type,amount,
                       symbol,occurred_at,metadata_json,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source_key) DO NOTHING""",
                (f"fill:{fill_key}:commission", order["telegram_id"], position.get("id"), order_id,
                 "COMMISSION", commission, order.get("symbol"), now,
                 '{"phase":"entry"}', now),
            )
            updated = dict(conn.execute("SELECT * FROM paper_execution_orders WHERE id=?", (order_id,)).fetchone())
        return FillResult(order=updated, fill=fill, position=position, created=True)


    def mark_to_market(self, position_id: int, *, last_price: float) -> dict[str, Any]:
        if last_price <= 0:
            raise ValueError("last_price must be positive")
        now = self._now()
        with connect() as conn:
            row = conn.execute("SELECT * FROM paper_execution_positions WHERE id=?", (position_id,)).fetchone()
            if row is None:
                raise KeyError(f"Unknown paper position: {position_id}")
            item = dict(row)
            if item["status"] in {PositionStatus.CLOSED.value, PositionStatus.CANCELLED.value, PositionStatus.FAILED.value}:
                return item
            qty = float(item.get("quantity") or 0.0)
            entry = float(item.get("average_entry") or 0.0)
            direction = 1.0 if str(item.get("side")).upper() == "LONG" else -1.0
            unrealized = (float(last_price) - entry) * qty * direction
            conn.execute("UPDATE paper_execution_positions SET last_price=?,unrealized_pnl=?,updated_at=? WHERE id=?",
                         (float(last_price), unrealized, now, position_id))
            updated = conn.execute("SELECT * FROM paper_execution_positions WHERE id=?", (position_id,)).fetchone()
        return dict(updated)

    def close_position(
        self, position_id: int, *, quantity: float, exit_price: float,
        commission_rate: float = DEFAULT_COMMISSION_RATE,
        event_key: str | None = None, reason: str = "MANUAL_CLOSE",
    ) -> dict[str, Any]:
        if quantity <= 0 or exit_price <= 0:
            raise ValueError("close quantity and exit_price must be positive")
        event_key = event_key or f"manual-close:{position_id}:{float(quantity):.12g}:{float(exit_price):.12g}"
        now = self._now()
        with connect() as conn:
            row = conn.execute("SELECT * FROM paper_execution_positions WHERE id=?", (position_id,)).fetchone()
            if row is None:
                raise KeyError(f"Unknown paper position: {position_id}")
            item = dict(row)
            existing = conn.execute(
                "SELECT id FROM paper_position_lifecycle_events WHERE event_key=?", (event_key,)
            ).fetchone()
            if existing is not None:
                return item
            current_qty = float(item.get("quantity") or 0.0)
            if item["status"] == PositionStatus.CLOSED.value or current_qty <= 0:
                return item
            applied = min(float(quantity), current_qty)
            entry = float(item.get("average_entry") or 0.0)
            direction = 1.0 if str(item.get("side")).upper() == "LONG" else -1.0
            realized_delta = (float(exit_price) - entry) * applied * direction
            close_commission = applied * float(exit_price) * max(0.0, float(commission_rate))
            remaining = max(0.0, current_qty - applied)
            status = PositionStatus.CLOSED.value if remaining <= 1e-12 else PositionStatus.PARTIALLY_CLOSED.value
            realized_total = float(item.get("realized_pnl") or 0.0) + realized_delta
            initial_risk = float(item.get("initial_risk_amount") or 0.0)
            realized_r_delta = realized_delta / initial_risk if initial_risk > 0 else 0.0
            realized_r_total = float(item.get("realized_r") or 0.0) + realized_r_delta
            commission_total = float(item.get("total_commission") or 0.0) + close_commission
            unrealized = 0.0 if status == PositionStatus.CLOSED.value else (float(exit_price)-entry)*remaining*direction
            conn.execute("""UPDATE paper_execution_positions SET status=?,quantity=?,last_price=?,realized_pnl=?,
                         unrealized_pnl=?,total_commission=?,remaining_fraction=?,realized_r=?,
                         close_reason=?,closed_at=?,updated_at=? WHERE id=?""",
                         (status, remaining, float(exit_price), realized_total, unrealized, commission_total,
                          remaining / float(item.get("initial_quantity") or current_qty), realized_r_total,
                          reason if status == PositionStatus.CLOSED.value else item.get("close_reason"),
                          now if status == PositionStatus.CLOSED.value else None, now, position_id))
            conn.execute(
                """INSERT INTO paper_position_lifecycle_events(
                       event_key,position_id,idempotency_key,telegram_id,signal_id,event_type,
                       from_status,to_status,signal_status,price,quantity_before,quantity_after,
                       realized_pnl_delta,realized_r_delta,commission_delta,reason,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event_key, position_id, item["idempotency_key"], item["telegram_id"],
                    item["signal_id"],
                    "CLOSED" if status == PositionStatus.CLOSED.value else "PARTIAL_CLOSED",
                    item["status"], status, None, float(exit_price), current_qty, remaining,
                    realized_delta, realized_r_delta, close_commission, reason, now,
                ),
            )
            self._write_close_ledger(conn, item, event_key, realized_delta, realized_r_delta,
                                     close_commission, float(exit_price), now)
            updated = conn.execute("SELECT * FROM paper_execution_positions WHERE id=?", (position_id,)).fetchone()
        return dict(updated)

    def apply_signal_transition(
        self,
        position_id: int,
        *,
        signal_status: str,
        price: float,
        event_key: str,
        reason: str | None = None,
        commission_rate: float = 0.0,
    ) -> PositionLifecycleResult:
        """Apply one signal lifecycle command exactly once to a unified position."""
        if price <= 0:
            raise ValueError("lifecycle price must be positive")
        normalized = str(signal_status or "").upper()
        now = self._now()
        with connect() as conn:
            existing = conn.execute(
                "SELECT * FROM paper_position_lifecycle_events WHERE event_key=?",
                (event_key,),
            ).fetchone()
            row = conn.execute(
                "SELECT * FROM paper_execution_positions WHERE id=?", (position_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown paper position: {position_id}")
            item = dict(row)
            if existing is not None:
                persisted = dict(existing)
                return PositionLifecycleResult(
                    position=item,
                    applied=False,
                    event_type=str(persisted["event_type"]),
                    realized_pnl_delta=float(persisted.get("realized_pnl_delta") or 0.0),
                    realized_r_delta=float(persisted.get("realized_r_delta") or 0.0),
                    event_key=str(persisted["event_key"]),
                )

            current_status = str(item["status"])
            current_qty = float(item.get("quantity") or 0.0)
            initial_qty = float(item.get("initial_quantity") or current_qty)
            if current_status in {
                PositionStatus.CLOSED.value,
                PositionStatus.CANCELLED.value,
                PositionStatus.FAILED.value,
            } or current_qty <= 0:
                return PositionLifecycleResult(item, False, "TERMINAL_NOOP", event_key=event_key)

            lifecycle_rank = {
                "WATCHING": 0, "TRIGGERED": 0, "ACTIVE": 0,
                "TP1": 1, "TP2": 2,
                **{status: 3 for status in self.TERMINAL_SIGNAL_STATUSES},
            }
            previous_signal_status = str(item.get("last_signal_status") or "").upper()
            if (
                previous_signal_status
                and lifecycle_rank.get(normalized, 0) < lifecycle_rank.get(previous_signal_status, 0)
            ):
                return PositionLifecycleResult(item, False, "STALE_NOOP", event_key=event_key)

            if normalized == "TP1":
                target_fraction = 0.5
            elif normalized == "TP2":
                target_fraction = 0.25
            elif normalized in self.TERMINAL_SIGNAL_STATUSES:
                target_fraction = 0.0
            else:
                target_fraction = current_qty / initial_qty if initial_qty > 0 else 1.0

            target_qty = min(current_qty, max(0.0, initial_qty * target_fraction))
            closed_qty = max(0.0, current_qty - target_qty)
            direction = 1.0 if str(item.get("side") or "").upper() == "LONG" else -1.0
            entry = float(item.get("average_entry") or 0.0)
            realized_delta = (float(price) - entry) * closed_qty * direction
            close_commission = closed_qty * float(price) * max(0.0, float(commission_rate))
            initial_risk = float(item.get("initial_risk_amount") or 0.0)
            realized_r_delta = realized_delta / initial_risk if initial_risk > 0 else 0.0
            realized_total = float(item.get("realized_pnl") or 0.0) + realized_delta
            realized_r_total = float(item.get("realized_r") or 0.0) + realized_r_delta
            commission_total = float(item.get("total_commission") or 0.0) + close_commission
            remaining_fraction = target_qty / initial_qty if initial_qty > 0 else 0.0
            terminal = target_qty <= 1e-12
            next_status = (
                PositionStatus.CLOSED.value if terminal
                else PositionStatus.PARTIALLY_CLOSED.value
                if closed_qty > 0
                else current_status
            )
            unrealized = (
                0.0 if terminal
                else (float(price) - entry) * target_qty * direction
            )
            event_type = (
                "CLOSED" if terminal
                else "PARTIAL_CLOSED" if closed_qty > 0
                else "MARKED"
            )
            conn.execute(
                """UPDATE paper_execution_positions
                   SET status=?,quantity=?,last_price=?,realized_pnl=?,realized_r=?,
                       unrealized_pnl=?,total_commission=?,remaining_fraction=?,
                       close_reason=?,last_signal_status=?,closed_at=?,updated_at=?
                   WHERE id=?""",
                (
                    next_status, target_qty, float(price), realized_total, realized_r_total,
                    unrealized, commission_total, remaining_fraction,
                    (reason or normalized) if terminal else item.get("close_reason"),
                    normalized, now if terminal else None, now, position_id,
                ),
            )
            conn.execute(
                """INSERT INTO paper_position_lifecycle_events(
                       event_key,position_id,idempotency_key,telegram_id,signal_id,event_type,
                       from_status,to_status,signal_status,price,quantity_before,quantity_after,
                       realized_pnl_delta,realized_r_delta,commission_delta,reason,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event_key, position_id, item["idempotency_key"], item["telegram_id"],
                    item["signal_id"], event_type, current_status, next_status, normalized,
                    float(price), current_qty, target_qty, realized_delta, realized_r_delta, close_commission,
                    reason or normalized, now,
                ),
            )
            self._write_close_ledger(conn, item, event_key, realized_delta, realized_r_delta,
                                     close_commission, float(price), now)
            updated = conn.execute(
                "SELECT * FROM paper_execution_positions WHERE id=?", (position_id,)
            ).fetchone()
        return PositionLifecycleResult(
            dict(updated), True, event_type, realized_delta, realized_r_delta, event_key
        )

    def recent_orders(self, telegram_id: int, limit: int = 20) -> list[dict[str, Any]]:
        safe = max(1, min(int(limit), 100))
        with connect() as conn:
            rows = conn.execute(f"SELECT * FROM paper_execution_orders WHERE telegram_id=? ORDER BY id DESC LIMIT {safe}", (telegram_id,)).fetchall()
        return [dict(row) for row in rows]

    def recent_fills(self, telegram_id: int, limit: int = 20) -> list[dict[str, Any]]:
        safe = max(1, min(int(limit), 100))
        with connect() as conn:
            rows = conn.execute(
                f"""SELECT f.*,o.symbol,o.side,o.timeframe,o.status AS order_status
                    FROM paper_execution_fills f JOIN paper_execution_orders o ON o.id=f.order_id
                    WHERE f.telegram_id=? ORDER BY f.id DESC LIMIT {safe}""", (telegram_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def recent_positions(self, telegram_id: int, limit: int = 20) -> list[dict[str, Any]]:
        safe = max(1, min(int(limit), 100))
        with connect() as conn:
            rows = conn.execute(f"SELECT * FROM paper_execution_positions WHERE telegram_id=? ORDER BY id DESC LIMIT {safe}", (telegram_id,)).fetchall()
        return [dict(row) for row in rows]

    def get_order_for_user(self, telegram_id: int, reference: str) -> dict[str, Any] | None:
        raw = str(reference).strip()
        with connect() as conn:
            if raw.isdigit():
                row = conn.execute("SELECT * FROM paper_execution_orders WHERE telegram_id=? AND (id=? OR signal_id=?) ORDER BY id DESC LIMIT 1", (telegram_id, int(raw), int(raw))).fetchone()
            else:
                row = conn.execute("SELECT * FROM paper_execution_orders WHERE telegram_id=? AND (order_key=? OR idempotency_key=? OR plan_id=? OR execution_ref=?) ORDER BY id DESC LIMIT 1", (telegram_id, raw, raw, raw, raw)).fetchone()
        return dict(row) if row else None

    def order_events(self, telegram_id: int, order_id: int) -> list[dict[str, Any]]:
        with connect() as conn:
            rows = conn.execute("SELECT * FROM paper_order_events WHERE telegram_id=? AND order_id=? ORDER BY id ASC", (telegram_id, order_id)).fetchall()
        return [dict(row) for row in rows]

    def position_for_order(self, order_id: int) -> dict[str, Any] | None:
        with connect() as conn:
            return self._position_for_order_conn(conn, order_id)

    def lifecycle_events(self, position_id: int) -> list[dict[str, Any]]:
        with connect() as conn:
            rows = conn.execute(
                """SELECT * FROM paper_position_lifecycle_events
                   WHERE position_id=? ORDER BY id ASC""",
                (position_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _upsert_position(self, conn, order: dict[str, Any], fill: dict[str, Any], cumulative_qty: float,
                         average_price: float, commission: float, now: str) -> dict[str, Any]:
        initial_quantity = float(order.get("requested_quantity") or cumulative_qty)
        remaining_fraction = cumulative_qty / initial_quantity if initial_quantity > 0 else 0.0
        conn.execute(
            """INSERT INTO paper_execution_positions(
                   position_key,order_id,idempotency_key,telegram_id,signal_id,symbol,timeframe,side,status,
                   quantity,average_entry,last_price,realized_pnl,unrealized_pnl,total_commission,
                   initial_quantity,stop_loss,initial_risk_amount,remaining_fraction,opened_at,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?, 'OPEN',?,?,?,0,0,?,?,?,?,?,?,?,?)
               ON CONFLICT(position_key) DO UPDATE SET quantity=?,average_entry=?,last_price=?,
                   remaining_fraction=?,total_commission=paper_execution_positions.total_commission+?,
                   updated_at=?""",
            (order["idempotency_key"], order["id"], order["idempotency_key"], order["telegram_id"], order["signal_id"],
             order["symbol"], order["timeframe"], order["side"], cumulative_qty, average_price, fill["price"],
             commission, initial_quantity, order.get("stop_loss"), order.get("risk_amount"),
             remaining_fraction, now, now, now,
             cumulative_qty, average_price, fill["price"], remaining_fraction, commission, now),
        )
        return self._position_for_order_conn(conn, int(order["id"])) or {}

    @staticmethod
    def _write_close_ledger(conn, position: dict[str, Any], event_key: str,
                            realized: float, realized_r: float, commission: float,
                            price: float, now: str) -> None:
        common = (position["telegram_id"], position["id"], position.get("order_id"),
                  position.get("symbol"), now, now)
        conn.execute(
            """INSERT INTO paper_portfolio_ledger(
                   source_key,telegram_id,position_id,order_id,entry_type,amount,realized_r_delta,
                   symbol,occurred_at,metadata_json,created_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source_key) DO NOTHING""",
            (f"lifecycle:{event_key}:realized", common[0], common[1], common[2], "REALIZED_PNL",
             realized, realized_r, common[3], common[4], '{"price":' + str(price) + '}', common[5]),
        )
        if commission:
            conn.execute(
                """INSERT INTO paper_portfolio_ledger(
                       source_key,telegram_id,position_id,order_id,entry_type,amount,
                       symbol,occurred_at,metadata_json,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source_key) DO NOTHING""",
                (f"lifecycle:{event_key}:commission", common[0], common[1], common[2], "COMMISSION",
                 commission, common[3], common[4], '{"phase":"exit"}', common[5]),
            )

    @staticmethod
    def _position_for_order_conn(conn, order_id: int) -> dict[str, Any] | None:
        row = conn.execute("SELECT * FROM paper_execution_positions WHERE order_id=?", (order_id,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _latest_fill_conn(conn, order_id: int) -> dict[str, Any] | None:
        row = conn.execute("SELECT * FROM paper_execution_fills WHERE order_id=? ORDER BY id DESC LIMIT 1", (order_id,)).fetchone()
        return dict(row) if row else None

    def _latest_fill(self, order_id: int) -> dict[str, Any] | None:
        with connect() as conn:
            return self._latest_fill_conn(conn, order_id)

    @staticmethod
    def _event(conn, order_id: int, from_status: OrderStatus | None, to_status: OrderStatus,
               actor: str, reason_code: str, reason: str | None = None) -> None:
        order = dict(conn.execute("SELECT telegram_id,idempotency_key FROM paper_execution_orders WHERE id=?", (order_id,)).fetchone())
        conn.execute(
            """INSERT INTO paper_order_events(order_id,idempotency_key,telegram_id,from_status,to_status,actor,reason_code,reason,created_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (order_id, order["idempotency_key"], order["telegram_id"],
             from_status.value if from_status else None, to_status.value, actor, reason_code, reason,
             datetime.now(timezone.utc).isoformat()),
        )
