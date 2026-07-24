from __future__ import annotations

from typing import Any

from database.database import connect


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

    def open_positions(self, telegram_id: int) -> list[dict[str, Any]]:
        with connect() as conn:
            rows = conn.execute(
                """SELECT * FROM paper_execution_positions
                   WHERE telegram_id=? AND status IN ('OPEN','PARTIAL') ORDER BY id ASC""",
                (telegram_id,),
            ).fetchall()
        return [dict(row) for row in rows]
