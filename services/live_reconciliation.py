from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from database.database import connect
from services.exchanges.base import ExchangeAdapter
from services.live_execution import LiveExecutionCoordinator
from services.live_safety import LiveAuditRepository, LiveKillSwitchRepository


OPEN_LOCAL_STATES = (
    "SUBMITTING", "SUBMITTED", "ACKNOWLEDGED", "PARTIALLY_FILLED",
    "UNKNOWN", "RECONCILING", "RECOVERY_REQUIRED",
)
UNRESOLVED_LOCAL_STATES = ("UNKNOWN", "RECONCILING", "RECOVERY_REQUIRED")


def _canonical(value: object) -> str:
    return "".join(char for char in str(value).upper() if char.isalnum())


class LiveReconciliationService:
    """Reconstruct local execution truth, then compare it with exchange truth."""

    async def reconcile(self, *, adapter: ExchangeAdapter, telegram_id: int,
                        account_id: int, exchange: str) -> dict[str, Any]:
        with connect() as conn:
            candidates = [dict(row) for row in conn.execute(
                f"""SELECT * FROM live_executions e WHERE account_id=? AND
                    (state IN ({','.join('?' for _ in OPEN_LOCAL_STATES)}) OR
                     (state='FILLED' AND EXISTS(SELECT 1 FROM live_execution_fills f
                        WHERE f.execution_id=e.id AND f.exchange_fill_id LIKE '__order_summary__:%')))
                    ORDER BY id""", (account_id, *OPEN_LOCAL_STATES)).fetchall()]

        coordinator = LiveExecutionCoordinator(adapter)
        repaired: list[dict[str, Any]] = []
        for candidate in candidates:
            before = str(candidate["state"])
            result = await coordinator.recover(int(candidate["id"]))
            if result.state.value != before:
                repaired.append({"execution_id": int(candidate["id"]),
                                 "from": before, "to": result.state.value})

        # Account snapshots are comparisons, not prerequisites for direct order/fill
        # recovery. Failure still propagates to the worker's fail-closed unavailable path.
        exchange_orders = await adapter.open_orders()
        exchange_positions = await adapter.positions()

        # Compare only after durable reconstruction.
        with connect() as conn:
            active_orders = [dict(row) for row in conn.execute(
                f"""SELECT * FROM live_executions WHERE account_id=? AND state IN
                    ({','.join('?' for _ in OPEN_LOCAL_STATES)}) ORDER BY id""",
                (account_id, *OPEN_LOCAL_STATES)).fetchall()]
            all_local_orders = [dict(row) for row in conn.execute(
                "SELECT * FROM live_executions WHERE account_id=? ORDER BY id", (account_id,)).fetchall()]
            ledger_positions = [dict(row) for row in conn.execute("""SELECT symbol,position_side,quantity
                FROM live_positions WHERE account_id=? AND status='OPEN' AND quantity>0""",
                (account_id,)).fetchall()]
            position_rows = [dict(row) for row in conn.execute("""SELECT symbol,side,position_side,
                reduce_only,executed_quantity,state FROM live_executions
                WHERE account_id=? AND executed_quantity>0
                  AND state NOT IN ('REJECTED','FAILED') ORDER BY id""", (account_id,)).fetchall()]

        mismatches: list[dict[str, Any]] = []
        for row in active_orders:
            if row["state"] in UNRESOLVED_LOCAL_STATES:
                mismatches.append({
                    "type": "EXECUTION_TRUTH_UNRESOLVED", "severity": "CRITICAL",
                    "local_ref": str(row["id"]), "exchange_ref": row.get("exchange_order_id"),
                    "symbol": row["symbol"], "evidence": row.get("recovery_reason"),
                })

        known_client_ids = {str(row.get("client_order_id") or "") for row in all_local_orders}
        known_order_ids = {str(row.get("exchange_order_id") or "") for row in all_local_orders
                           if row.get("exchange_order_id")}
        for order in exchange_orders:
            if (str(order.order_id) not in known_order_ids
                    and str(order.client_order_id or "") not in known_client_ids):
                mismatches.append({
                    "type": "MISSING_LOCAL_ORDER", "severity": "CRITICAL",
                    "local_ref": None, "exchange_ref": order.order_id, "symbol": order.symbol,
                })

        local_positions: dict[tuple[str, str], Decimal] = {
            (_canonical(row["symbol"]), str(row["position_side"]).upper()):
                Decimal(str(row["quantity"])) for row in ledger_positions
        }
        if not ledger_positions:
            for row in position_rows:
                side = str(row.get("position_side") or "").upper()
                if side not in {"LONG", "SHORT"}:
                    raw_side = str(row.get("side") or "").upper()
                    side = (("LONG" if raw_side == "SELL" else "SHORT")
                            if bool(row.get("reduce_only"))
                            else ("LONG" if raw_side == "BUY" else "SHORT"))
                key = (_canonical(row["symbol"]), side)
                delta = Decimal(str(row.get("executed_quantity") or 0))
                local_positions[key] = local_positions.get(key, Decimal("0")) + (
                    -delta if bool(row.get("reduce_only")) else delta)

        exchange_position_map = {
            (_canonical(position.symbol), str(position.side).upper()): Decimal(position.quantity)
            for position in exchange_positions if position.quantity > 0
        }
        tolerance = Decimal("0.00000001")
        for key, quantity in exchange_position_map.items():
            local_quantity = local_positions.get(key)
            if local_quantity is None or local_quantity <= 0:
                mismatches.append({
                    "type": "UNKNOWN_EXCHANGE_POSITION", "severity": "CRITICAL",
                    "local_ref": None, "exchange_ref": f"{key[0]}:{key[1]}",
                    "symbol": key[0], "exchange_quantity": str(quantity),
                })
            elif abs(local_quantity - quantity) > tolerance:
                mismatches.append({
                    "type": "POSITION_QTY_MISMATCH", "severity": "CRITICAL",
                    "local_ref": f"{key[0]}:{key[1]}", "exchange_ref": f"{key[0]}:{key[1]}",
                    "symbol": key[0], "local_quantity": str(local_quantity),
                    "exchange_quantity": str(quantity),
                })
        for key, quantity in local_positions.items():
            if quantity > 0 and key not in exchange_position_map:
                mismatches.append({
                    "type": "UNKNOWN_LOCAL_POSITION", "severity": "CRITICAL",
                    "local_ref": f"{key[0]}:{key[1]}", "exchange_ref": None,
                    "symbol": key[0], "local_quantity": str(quantity),
                })

        now = datetime.now(timezone.utc).isoformat()
        active_event_keys: set[str] = set()
        with connect() as conn:
            for mismatch in mismatches:
                identity = json.dumps({
                    "account_id": account_id, "type": mismatch["type"],
                    "local_ref": mismatch.get("local_ref"),
                    "exchange_ref": mismatch.get("exchange_ref"),
                    "symbol": mismatch.get("symbol"),
                    "local_quantity": mismatch.get("local_quantity"),
                    "exchange_quantity": mismatch.get("exchange_quantity"),
                }, sort_keys=True, separators=(",", ":"))
                event_key = "recon-" + hashlib.sha256(identity.encode()).hexdigest()
                active_event_keys.add(event_key)
                conn.execute("""INSERT INTO live_reconciliation_events(event_key,telegram_id,
                    account_id,exchange,mismatch_type,severity,local_ref,exchange_ref,details_json,
                    created_at) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(event_key) DO UPDATE SET
                    severity=excluded.severity,details_json=excluded.details_json,resolved_at=NULL""", (
                    event_key, telegram_id, account_id, exchange, mismatch["type"],
                    mismatch["severity"], mismatch.get("local_ref"), mismatch.get("exchange_ref"),
                    json.dumps({"symbol": mismatch.get("symbol"),
                               "local_quantity": mismatch.get("local_quantity"),
                               "exchange_quantity": mismatch.get("exchange_quantity"),
                               "evidence": mismatch.get("evidence"),
                               "automatic_repair": False}, sort_keys=True), now))
            if active_event_keys:
                placeholders = ",".join("?" for _ in active_event_keys)
                conn.execute(f"""UPDATE live_reconciliation_events SET resolved_at=?
                    WHERE account_id=? AND resolved_at IS NULL AND event_key NOT IN ({placeholders})""",
                    (now, account_id, *sorted(active_event_keys)))
            else:
                conn.execute("""UPDATE live_reconciliation_events SET resolved_at=?
                    WHERE account_id=? AND resolved_at IS NULL""", (now, account_id))
            if mismatches:
                conn.execute("""UPDATE live_exchange_accounts SET live_enabled=0,kill_switch=1,
                    lifecycle_state='SUSPENDED',updated_at=? WHERE id=? AND telegram_id=?""",
                    (now, account_id, telegram_id))
        if mismatches:
            LiveKillSwitchRepository().set(scope="CONNECTION", scope_key=str(account_id), active=True,
                                           reason_code="RECONCILIATION_MISMATCH")
        outcome = "MISMATCH" if mismatches else "MATCHED"
        LiveAuditRepository().record(
            event_type="RECONCILIATION", outcome=outcome,
            telegram_id=telegram_id, account_id=account_id, exchange=exchange,
            metadata={"local_orders": len(active_orders), "exchange_orders": len(exchange_orders),
                      "exchange_positions": len(exchange_positions),
                      "mismatch_types": [item["type"] for item in mismatches],
                      "repaired_executions": repaired,
                      "automatic_repair": bool(repaired)})
        return {"status": outcome, "mismatches": mismatches,
                "new_entries_blocked": bool(mismatches),
                "automatic_repair": bool(repaired), "repaired_executions": repaired,
                "exchange_authoritative": True}
