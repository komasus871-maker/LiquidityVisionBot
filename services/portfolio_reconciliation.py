from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

from database.database import connect


@dataclass(frozen=True)
class ReconciliationReport:
    telegram_id: int
    legacy_open_before: int
    legacy_open_after: int
    unified_open: int
    stale_legacy_closed: int
    mismatch: bool
    status: str
    reconciled_at: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class PortfolioReconciliationService:
    """Conservative bridge between legacy paper positions and unified lifecycle.

    It never fabricates orders or fills. Legacy rows are only removed from risk
    when their authoritative signal is already terminal.
    """

    TERMINAL_SIGNAL_STATUSES = {"TP3", "STOP", "BREAKEVEN", "INVALIDATED", "EXPIRED", "CLOSED"}

    def reconcile(self, telegram_id: int) -> ReconciliationReport:
        now = datetime.now(timezone.utc).isoformat()
        with connect() as conn:
            before = conn.execute(
                "SELECT COUNT(*) FROM paper_positions WHERE telegram_id=? AND status IN ('OPEN','PARTIAL')",
                (telegram_id,),
            ).fetchone()
            stale = conn.execute(
                """SELECT p.id, s.status AS signal_status
                   FROM paper_positions p
                   JOIN signals s ON s.id=p.signal_id
                   WHERE p.telegram_id=? AND p.status IN ('OPEN','PARTIAL')
                     AND UPPER(COALESCE(s.status,'')) IN ('TP3','STOP','BREAKEVEN','INVALIDATED','EXPIRED','CLOSED')""",
                (telegram_id,),
            ).fetchall()
            for row in stale:
                signal_status = str(row["signal_status"] if isinstance(row, dict) else row[1]).upper()
                position_id = int(row["id"] if isinstance(row, dict) else row[0])
                conn.execute(
                    """UPDATE paper_positions
                       SET status='CLOSED', close_reason=?, last_signal_status=?, closed_at=COALESCE(closed_at,?), updated_at=?
                       WHERE id=? AND status IN ('OPEN','PARTIAL')""",
                    (f"RECONCILED_{signal_status}", signal_status, now, now, position_id),
                )
            after = conn.execute(
                "SELECT COUNT(*) FROM paper_positions WHERE telegram_id=? AND status IN ('OPEN','PARTIAL')",
                (telegram_id,),
            ).fetchone()
            unified = conn.execute(
                """SELECT COUNT(*) FROM paper_execution_positions
                   WHERE telegram_id=? AND status IN ('OPEN','PARTIALLY_FILLED','PARTIALLY_CLOSED')""",
                (telegram_id,),
            ).fetchone()
        legacy_before = int(before[0] or 0)
        legacy_after = int(after[0] or 0)
        unified_open = int(unified[0] or 0)
        return ReconciliationReport(
            telegram_id=int(telegram_id), legacy_open_before=legacy_before,
            legacy_open_after=legacy_after, unified_open=unified_open,
            stale_legacy_closed=len(stale), mismatch=legacy_after != unified_open,
            status="MISMATCH" if legacy_after != unified_open else "CONSISTENT",
            reconciled_at=now,
        )
