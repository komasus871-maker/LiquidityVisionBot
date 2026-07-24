from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from database.database import connect


@dataclass(frozen=True)
class ReconciliationReport:
    telegram_id: int
    legacy_open_count: int
    unified_open_count: int
    confirmed_active_legacy_count: int
    stale_legacy_closed_count: int
    unresolved_legacy_count: int
    confirmed_active_heat_r: float
    unresolved_heat_r: float
    status: str
    mismatch_detected: bool
    reconciled_at: str
    lifecycle_mismatch_count: int = 0

    # Compatibility aliases retained for v9.9.6.6 callers and tests.
    @property
    def legacy_open_before(self) -> int:
        return self.legacy_open_count + self.stale_legacy_closed_count

    @property
    def legacy_open_after(self) -> int:
        return self.legacy_open_count

    @property
    def unified_open(self) -> int:
        return self.unified_open_count

    @property
    def stale_legacy_closed(self) -> int:
        return self.stale_legacy_closed_count

    @property
    def mismatch(self) -> bool:
        return self.mismatch_detected

    @property
    def portfolio_state_resolved(self) -> bool:
        return self.unresolved_legacy_count == 0

    @property
    def heat_source(self) -> str:
        if self.confirmed_active_legacy_count and self.unresolved_legacy_count:
            return "LEGACY_CONFIRMED+UNRESOLVED"
        if self.unresolved_legacy_count:
            return "LEGACY_UNRESOLVED"
        if self.confirmed_active_legacy_count:
            return "LEGACY_CONFIRMED"
        if self.unified_open_count:
            return "UNIFIED_COUNT_ONLY"
        return "EMPTY"

    @property
    def lifecycle_authority(self) -> str:
        return "UNIFIED_WITH_LEGACY_PROJECTION"

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.update(
            legacy_open_before=self.legacy_open_before,
            legacy_open_after=self.legacy_open_after,
            unified_open=self.unified_open,
            stale_legacy_closed=self.stale_legacy_closed,
            mismatch=self.mismatch,
            portfolio_state_resolved=self.portfolio_state_resolved,
            heat_source=self.heat_source,
            lifecycle_authority=self.lifecycle_authority,
        )
        return data


class PortfolioReconciliationService:
    """Conservative bridge between legacy paper positions and unified lifecycle.

    It never fabricates orders, fills, prices, or positions. A legacy row is
    removed from risk only when its source signal is authoritatively terminal.
    Unknown or missing signal state remains unresolved and therefore fail-closed.
    """

    TERMINAL_SIGNAL_STATUSES = {"TP3", "STOP", "BREAKEVEN", "INVALIDATED", "EXPIRED", "CLOSED"}
    ACTIVE_SIGNAL_STATUSES = {"ACTIVE", "TP1", "TP2"}

    def reconcile(self, telegram_id: int) -> ReconciliationReport:
        now = datetime.now(timezone.utc).isoformat()
        stale_closed = 0
        lifecycle_mismatches = 0
        with connect() as conn:
            rows = conn.execute(
                """SELECT p.id, p.initial_risk_r, p.remaining_fraction,
                          UPPER(COALESCE(s.status,'')) AS signal_status,
                          u.id AS unified_position_id, UPPER(COALESCE(u.status,'')) AS unified_status
                   FROM paper_positions p
                   LEFT JOIN signals s ON s.id=p.signal_id
                   LEFT JOIN paper_execution_positions u
                     ON u.telegram_id=p.telegram_id AND u.signal_id=p.signal_id
                   WHERE p.telegram_id=? AND p.status IN ('OPEN','PARTIAL')""",
                (telegram_id,),
            ).fetchall()

            for row in rows:
                item = dict(row)
                signal_status = str(item.get("signal_status") or "").upper()
                if signal_status not in self.TERMINAL_SIGNAL_STATUSES:
                    continue
                if item.get("unified_position_id") is not None and str(
                    item.get("unified_status") or ""
                ) not in {"CLOSED", "CANCELLED", "FAILED"}:
                    lifecycle_mismatches += 1
                    continue
                cursor = conn.execute(
                    """UPDATE paper_positions
                       SET status='CLOSED', close_reason=?, last_signal_status=?,
                           closed_at=COALESCE(closed_at,?), updated_at=?
                       WHERE id=? AND status IN ('OPEN','PARTIAL')""",
                    (
                        f"RECONCILED_{signal_status}", signal_status, now, now,
                        int(item["id"]),
                    ),
                )
                stale_closed += max(0, int(cursor.rowcount or 0))

            remaining = conn.execute(
                """SELECT p.id, p.initial_risk_r, p.remaining_fraction,
                          UPPER(COALESCE(s.status,'')) AS signal_status
                   FROM paper_positions p
                   LEFT JOIN signals s ON s.id=p.signal_id
                   WHERE p.telegram_id=? AND p.status IN ('OPEN','PARTIAL')""",
                (telegram_id,),
            ).fetchall()
            unified = conn.execute(
                """SELECT COUNT(*) FROM paper_execution_positions
                   WHERE telegram_id=? AND status IN ('OPEN','PARTIALLY_FILLED','PARTIALLY_CLOSED')""",
                (telegram_id,),
            ).fetchone()

        confirmed_count = 0
        unresolved_count = 0
        confirmed_heat = 0.0
        unresolved_heat = 0.0
        for row in remaining:
            item = dict(row)
            heat = float(item.get("initial_risk_r") or 0.0) * float(item.get("remaining_fraction") or 0.0)
            signal_status = str(item.get("signal_status") or "").upper()
            if signal_status in self.ACTIVE_SIGNAL_STATUSES:
                confirmed_count += 1
                confirmed_heat += heat
            else:
                unresolved_count += 1
                unresolved_heat += heat

        legacy_open = confirmed_count + unresolved_count
        unified_open = int(unified[0] or 0)
        mismatch = legacy_open != unified_open
        if unresolved_count:
            status = "UNRESOLVED"
        elif mismatch:
            status = "MISMATCH"
        else:
            status = "CONSISTENT"

        return ReconciliationReport(
            telegram_id=int(telegram_id),
            legacy_open_count=legacy_open,
            unified_open_count=unified_open,
            confirmed_active_legacy_count=confirmed_count,
            stale_legacy_closed_count=stale_closed,
            unresolved_legacy_count=unresolved_count,
            confirmed_active_heat_r=confirmed_heat,
            unresolved_heat_r=unresolved_heat,
            status=status,
            mismatch_detected=mismatch,
            reconciled_at=now,
            lifecycle_mismatch_count=lifecycle_mismatches,
        )
