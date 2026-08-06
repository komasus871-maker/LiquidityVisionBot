from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from database.database import connect


class HistoricalClassification(str, Enum):
    FULLY_RECONSTRUCTABLE = "FULLY_RECONSTRUCTABLE"
    PARTIALLY_RECONSTRUCTABLE = "PARTIALLY_RECONSTRUCTABLE"
    LEGACY_ONLY = "LEGACY_ONLY"
    AMBIGUOUS = "AMBIGUOUS"
    INVALID = "INVALID"


@dataclass(frozen=True)
class HistoricalMigrationReport:
    run_key: str
    scanned: int
    migrated: int
    skipped: int
    unresolved: int
    classifications: dict[str, int]
    last_legacy_position_id: int | None
    complete: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class HistoricalExecutionMigrationService:
    """Truth-preserving legacy catalog migration.

    This service never creates an order, fill, lifecycle event, position, fee,
    or ledger entry. It catalogs only factual legacy columns and links an
    already-existing unified position when the identity is unambiguous.
    """

    VALID_STATUSES = {"OPEN", "PARTIAL", "CLOSED", "REJECTED"}
    VALID_SIDES = {"LONG", "SHORT"}

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def run(self, *, batch_size: int = 500, after_id: int | None = None) -> HistoricalMigrationReport:
        safe_batch = max(1, min(int(batch_size), 5000))
        started = self._now()
        counts: Counter[str] = Counter()
        migrated = skipped = unresolved = 0
        last_id: int | None = None
        with connect() as conn:
            if after_id is None:
                previous = conn.execute(
                    """SELECT scanned_count,last_legacy_position_id FROM historical_migration_runs
                       WHERE status='COMPLETED' ORDER BY id DESC LIMIT 1"""
                ).fetchone()
                after_id = int(previous[1] or 0) if previous and int(previous[0] or 0) >= safe_batch else 0
            cursor_id = max(0, int(after_id))
            run_key = f"legacy-paper-positions:{cursor_id}:{safe_batch}:{started}"
            conn.execute(
                """INSERT INTO historical_migration_runs(
                       run_key,status,started_at,last_legacy_position_id
                   ) VALUES(?,'RUNNING',?,?)""", (run_key, started, cursor_id or None),
            )
            rows = conn.execute(
                """SELECT p.*, s.id AS source_signal_id
                   FROM paper_positions p LEFT JOIN signals s ON s.id=p.signal_id
                   WHERE p.id>? ORDER BY p.id ASC LIMIT ?""", (cursor_id, safe_batch),
            ).fetchall()
            for raw in rows:
                row = dict(raw)
                last_id = int(row["id"])
                classification, reason, linked_id = self._classify(conn, row)
                counts[classification.value] += 1
                unresolved += int(classification in {
                    HistoricalClassification.AMBIGUOUS, HistoricalClassification.INVALID,
                })
                payload = self._payload(row, classification, reason, linked_id)
                checksum = hashlib.sha256(
                    json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
                ).hexdigest()
                existing = conn.execute(
                    "SELECT source_checksum FROM historical_execution_records WHERE legacy_position_id=?",
                    (last_id,),
                ).fetchone()
                if existing is not None and str(existing[0]) == checksum:
                    skipped += 1
                    continue
                now = self._now()
                conn.execute(
                    """INSERT INTO historical_execution_records(
                           source_key,legacy_position_id,telegram_id,signal_id,linked_unified_position_id,
                           classification,migration_status,symbol,timeframe,side,legacy_status,
                           entry_price,exit_price,quantity,notional,risk_amount,realized_pnl,realized_r,
                           commission,opened_at,closed_at,source_created_at,price_provenance,
                           risk_provenance,commission_provenance,provenance_json,source_checksum,
                           migrated_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(legacy_position_id) DO UPDATE SET
                           linked_unified_position_id=excluded.linked_unified_position_id,
                           classification=excluded.classification,migration_status=excluded.migration_status,
                           symbol=excluded.symbol,timeframe=excluded.timeframe,side=excluded.side,
                           legacy_status=excluded.legacy_status,entry_price=excluded.entry_price,
                           exit_price=excluded.exit_price,quantity=excluded.quantity,notional=excluded.notional,
                           risk_amount=excluded.risk_amount,realized_pnl=excluded.realized_pnl,
                           realized_r=excluded.realized_r,opened_at=excluded.opened_at,
                           closed_at=excluded.closed_at,source_created_at=excluded.source_created_at,
                           price_provenance=excluded.price_provenance,risk_provenance=excluded.risk_provenance,
                           commission_provenance=excluded.commission_provenance,
                           provenance_json=excluded.provenance_json,source_checksum=excluded.source_checksum,
                           updated_at=excluded.updated_at""",
                    (
                        f"paper_positions:{last_id}", last_id, row["telegram_id"], row.get("signal_id"),
                        linked_id, classification.value,
                        "RESOLVED" if classification not in {HistoricalClassification.AMBIGUOUS, HistoricalClassification.INVALID} else "UNRESOLVED",
                        row.get("symbol"), row.get("timeframe"), row.get("side"), row.get("status"),
                        row.get("entry_price"), row.get("exit_price"), row.get("quantity"), row.get("notional"),
                        row.get("risk_amount"), row.get("realized_pnl"), row.get("realized_r"), None,
                        row.get("opened_at"), row.get("closed_at"), row.get("created_at"),
                        "LEGACY_RECORDED", "LEGACY_RECORDED" if row.get("risk_amount") is not None else "UNKNOWN",
                        "UNKNOWN", json.dumps(payload, sort_keys=True, ensure_ascii=False), checksum, now, now,
                    ),
                )
                migrated += 1
            complete = len(rows) < safe_batch
            conn.execute(
                """UPDATE historical_migration_runs SET status='COMPLETED',scanned_count=?,
                       migrated_count=?,skipped_count=?,unresolved_count=?,classification_json=?,
                       last_legacy_position_id=?,completed_at=? WHERE run_key=?""",
                (len(rows), migrated, skipped, unresolved, json.dumps(dict(counts), sort_keys=True),
                 last_id, self._now(), run_key),
            )
        return HistoricalMigrationReport(run_key, len(rows), migrated, skipped, unresolved,
                                         dict(counts), last_id, complete)

    def _classify(self, conn, row: dict[str, Any]) -> tuple[HistoricalClassification, str, int | None]:
        status = str(row.get("status") or "").upper()
        side = str(row.get("side") or "").upper()
        quantity = row.get("quantity")
        if status not in self.VALID_STATUSES or side not in self.VALID_SIDES:
            return HistoricalClassification.INVALID, "invalid status or side", None
        if quantity is not None and float(quantity) < 0:
            return HistoricalClassification.INVALID, "negative quantity", None
        unified = conn.execute(
            """SELECT id,symbol,side,status,realized_pnl,realized_r FROM paper_execution_positions
               WHERE telegram_id=? AND signal_id=? ORDER BY id""",
            (row["telegram_id"], row.get("signal_id")),
        ).fetchall()
        if len(unified) == 1:
            linked = unified[0]
            legacy_terminal = status == "CLOSED"
            unified_terminal = str(linked[3] or "").upper() == "CLOSED"
            identity_matches = (
                str(row.get("symbol") or "").strip().upper() == str(linked[1] or "").strip().upper()
                and side == str(linked[2] or "").strip().upper()
                and legacy_terminal == unified_terminal
            )
            if not identity_matches:
                return HistoricalClassification.AMBIGUOUS, "legacy and unified identity or lifecycle mismatch", None
            return HistoricalClassification.FULLY_RECONSTRUCTABLE, "linked to authoritative unified position", int(linked[0])
        if len(unified) > 1:
            return HistoricalClassification.AMBIGUOUS, "multiple unified positions share identity", None
        if row.get("source_signal_id") is None:
            return HistoricalClassification.AMBIGUOUS, "source signal missing", None
        if status == "REJECTED":
            return HistoricalClassification.LEGACY_ONLY, "historical rejected outcome; not an economic position", None
        if row.get("entry_price") is not None and quantity is not None:
            return HistoricalClassification.PARTIALLY_RECONSTRUCTABLE, "position facts known; fills and commissions unknown", None
        return HistoricalClassification.LEGACY_ONLY, "insufficient factual execution coverage", None

    @staticmethod
    def _payload(row: dict[str, Any], classification: HistoricalClassification,
                 reason: str, linked_id: int | None) -> dict[str, Any]:
        return {
            "source": {"table": "paper_positions", "id": int(row["id"])},
            "classification": classification.value, "reason": reason,
            "linked_unified_position_id": linked_id,
            "coverage": {
                "position": linked_id is not None,
                "entry_price": row.get("entry_price") is not None,
                "exit_price": row.get("exit_price") is not None,
                "quantity": row.get("quantity") is not None,
                "risk": row.get("risk_amount") is not None,
                "realized_pnl": row.get("realized_pnl") is not None,
                "realized_r": row.get("realized_r") is not None,
                "fills": False, "commissions": False, "lifecycle_events": False,
            },
        }

    def latest_report(self) -> dict[str, Any]:
        with connect() as conn:
            row = conn.execute(
                "SELECT * FROM historical_migration_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
            totals = conn.execute(
                """SELECT classification,COUNT(*) AS count FROM historical_execution_records
                   GROUP BY classification ORDER BY classification"""
            ).fetchall()
        return {
            "latest_run": dict(row) if row else None,
            "classifications": {str(item[0]): int(item[1]) for item in totals},
        }
