from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from database.database import connect
from services.exchanges.base import ExchangeAdapter
from services.exchanges.models import ExchangeCapability
from services.live_safety import LiveAuditRepository, LiveKillSwitchRepository


OPEN_LOCAL_STATES = ("SUBMITTED", "ACKNOWLEDGED", "PARTIALLY_FILLED", "UNKNOWN", "RECOVERY_REQUIRED")


class LiveReconciliationService:
    """Compare exchange truth to bounded local state and fail closed on mismatches."""

    async def reconcile(self, *, adapter: ExchangeAdapter, telegram_id: int,
                        account_id: int, exchange: str) -> dict[str, Any]:
        exchange_orders = await adapter.open_orders()
        exchange_positions = await adapter.positions()
        with connect() as conn:
            local_orders = [dict(row) for row in conn.execute(
                f"""SELECT * FROM live_executions WHERE account_id=? AND state IN
                    ({','.join('?' for _ in OPEN_LOCAL_STATES)}) ORDER BY id""",
                (account_id, *OPEN_LOCAL_STATES)).fetchall()]
            ledger_positions = [dict(row) for row in conn.execute("""SELECT symbol,position_side,quantity
                FROM live_positions WHERE account_id=? AND status='OPEN' AND quantity>0""",
                (account_id,)).fetchall()]
            position_rows = [dict(row) for row in conn.execute("""SELECT symbol,side,position_side,
                reduce_only,executed_quantity,state FROM live_executions
                WHERE account_id=? AND executed_quantity>0
                  AND state NOT IN ('REJECTED','FAILED') ORDER BY id""", (account_id,)).fetchall()]
        local_by_client = {str(row.get("client_order_id") or ""): row for row in local_orders}
        exchange_by_client = {str(order.client_order_id or ""): order for order in exchange_orders
                              if order.client_order_id}
        mismatches: list[dict[str, Any]] = []
        for client_id, row in local_by_client.items():
            if client_id not in exchange_by_client and row["state"] not in {"UNKNOWN", "RECOVERY_REQUIRED"}:
                mismatches.append({"type": "MISSING_EXCHANGE_ORDER", "severity": "CRITICAL",
                                   "local_ref": str(row["id"]), "exchange_ref": None,
                                   "symbol": row["symbol"]})
        for client_id, order in exchange_by_client.items():
            if client_id not in local_by_client:
                mismatches.append({"type": "MISSING_LOCAL_ORDER", "severity": "CRITICAL",
                                   "local_ref": None, "exchange_ref": order.order_id,
                                   "symbol": order.symbol})
        for client_id in sorted(set(local_by_client) & set(exchange_by_client)):
            local = local_by_client[client_id]
            order = exchange_by_client[client_id]
            local_executed = Decimal(str(local.get("executed_quantity") or 0))
            if abs(local_executed - Decimal(order.executed_quantity)) > Decimal("0.00000001"):
                mismatches.append({"type": "ORDER_STATUS_MISMATCH", "severity": "CRITICAL",
                                   "local_ref": str(local["id"]), "exchange_ref": order.order_id,
                                   "symbol": order.symbol, "local_quantity": str(local_executed),
                                   "exchange_quantity": str(order.executed_quantity)})
            if (adapter.capabilities().supports(ExchangeCapability.FILLS)
                    and local.get("exchange_order_id")):
                exchange_fills = await adapter.fills(
                    symbol=str(local["symbol"]), order_id=str(local["exchange_order_id"]))
                exchange_fill_quantity = sum(
                    (Decimal(fill.quantity) for fill in exchange_fills), Decimal("0"))
                if abs(local_executed - exchange_fill_quantity) > Decimal("0.00000001"):
                    mismatches.append({"type": "ORDER_STATUS_MISMATCH", "severity": "CRITICAL",
                                       "local_ref": str(local["id"]), "exchange_ref": order.order_id,
                                       "symbol": order.symbol, "local_quantity": str(local_executed),
                                       "exchange_quantity": str(exchange_fill_quantity),
                                       "evidence": "EXCHANGE_FILL_AGGREGATE"})
                with connect() as conn:
                    local_fee_row = conn.execute("""SELECT COALESCE(SUM(commission),0) fee
                        FROM live_execution_fills WHERE execution_id=?""", (local["id"],)).fetchone()
                local_fees = Decimal(str(local_fee_row["fee"] or 0))
                exchange_fees = sum((abs(Decimal(fill.commission)) for fill in exchange_fills),
                                    Decimal("0"))
                if abs(local_fees - exchange_fees) > Decimal("0.00000001"):
                    mismatches.append({"type": "FEE_MISMATCH", "severity": "CRITICAL",
                                       "local_ref": str(local["id"]), "exchange_ref": order.order_id,
                                       "symbol": order.symbol, "local_quantity": str(local_fees),
                                       "exchange_quantity": str(exchange_fees),
                                       "evidence": "EXCHANGE_FILL_FEES"})
        canonical = lambda value: "".join(char for char in str(value).upper() if char.isalnum())
        local_positions: dict[tuple[str, str], Decimal] = {
            (canonical(row["symbol"]), str(row["position_side"]).upper()): Decimal(str(row["quantity"]))
            for row in ledger_positions}
        if not ledger_positions:
            for row in position_rows:
                side = str(row.get("position_side") or "").upper()
                if side not in {"LONG", "SHORT"}:
                    raw_side = str(row.get("side") or "").upper()
                    side = (("LONG" if raw_side == "SELL" else "SHORT")
                            if bool(row.get("reduce_only"))
                            else ("LONG" if raw_side == "BUY" else "SHORT"))
                key = (canonical(row["symbol"]), side)
                delta = Decimal(str(row.get("executed_quantity") or 0))
                if bool(row.get("reduce_only")):
                    delta = -delta
                local_positions[key] = local_positions.get(key, Decimal("0")) + delta
        exchange_position_map = {
            (canonical(position.symbol), str(position.side).upper()): Decimal(position.quantity)
            for position in exchange_positions if position.quantity > 0}
        tolerance = Decimal("0.00000001")
        for key, quantity in exchange_position_map.items():
            local_quantity = local_positions.get(key)
            if local_quantity is None or local_quantity <= 0:
                mismatches.append({"type": "UNKNOWN_EXCHANGE_POSITION", "severity": "CRITICAL",
                                   "local_ref": None, "exchange_ref": f"{key[0]}:{key[1]}",
                                   "symbol": key[0], "exchange_quantity": str(quantity)})
            elif abs(local_quantity - quantity) > tolerance:
                mismatches.append({"type": "POSITION_QTY_MISMATCH", "severity": "CRITICAL",
                                   "local_ref": f"{key[0]}:{key[1]}",
                                   "exchange_ref": f"{key[0]}:{key[1]}", "symbol": key[0],
                                   "local_quantity": str(local_quantity),
                                   "exchange_quantity": str(quantity)})
        for key, quantity in local_positions.items():
            if quantity > 0 and key not in exchange_position_map:
                mismatches.append({"type": "UNKNOWN_LOCAL_POSITION", "severity": "CRITICAL",
                                   "local_ref": f"{key[0]}:{key[1]}", "exchange_ref": None,
                                   "symbol": key[0], "local_quantity": str(quantity)})
        now = datetime.now(timezone.utc).isoformat()
        with connect() as conn:
            for mismatch in mismatches:
                conn.execute("""INSERT INTO live_reconciliation_events(event_key,telegram_id,
                    account_id,exchange,mismatch_type,severity,local_ref,exchange_ref,details_json,
                    created_at) VALUES(?,?,?,?,?,?,?,?,?,?)""", (
                    str(uuid.uuid4()), telegram_id, account_id, exchange, mismatch["type"],
                    mismatch["severity"], mismatch.get("local_ref"), mismatch.get("exchange_ref"),
                    json.dumps({"symbol": mismatch.get("symbol"),
                               "local_quantity": mismatch.get("local_quantity"),
                               "exchange_quantity": mismatch.get("exchange_quantity"),
                               "evidence": mismatch.get("evidence"),
                               "automatic_repair": False},
                               sort_keys=True), now))
            if mismatches:
                conn.execute("""UPDATE live_exchange_accounts SET live_enabled=0,kill_switch=1,
                    lifecycle_state='SUSPENDED',updated_at=? WHERE id=? AND telegram_id=?""",
                    (now, account_id, telegram_id))
        if mismatches:
            LiveKillSwitchRepository().set(scope="CONNECTION", scope_key=str(account_id), active=True,
                                           reason_code="RECONCILIATION_MISMATCH")
        LiveAuditRepository().record(
            event_type="RECONCILIATION", outcome="MISMATCH" if mismatches else "MATCHED",
            telegram_id=telegram_id, account_id=account_id, exchange=exchange,
            metadata={"local_orders": len(local_orders), "exchange_orders": len(exchange_orders),
                      "exchange_positions": len(exchange_positions),
                      "mismatch_types": [item["type"] for item in mismatches],
                      "automatic_repair": False})
        return {"status": "MISMATCH" if mismatches else "MATCHED",
                "mismatches": mismatches, "new_entries_blocked": bool(mismatches),
                "automatic_repair": False, "exchange_authoritative": True}
