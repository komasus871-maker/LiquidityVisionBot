from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from database.database import connect


@dataclass(frozen=True)
class RejectionBucket:
    key: str
    count: int
    share_pct: float


class RejectionAnalytics:
    """Pure aggregation layer for copy-execution rejection diagnostics."""

    @staticmethod
    def build(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
        records = list(rows)
        total = len(records)
        codes = Counter(str(row.get("rejection_code") or "UNKNOWN") for row in records)
        symbols = Counter(str(row.get("symbol") or "UNKNOWN") for row in records)
        timeframes = Counter(str(row.get("timeframe") or "UNKNOWN") for row in records)

        def buckets(counter: Counter[str], limit: int = 5) -> list[RejectionBucket]:
            return [
                RejectionBucket(key=key, count=count, share_pct=(count / total * 100.0) if total else 0.0)
                for key, count in counter.most_common(limit)
            ]

        return {
            "total": total,
            "by_code": buckets(codes),
            "by_symbol": buckets(symbols),
            "by_timeframe": buckets(timeframes),
            "top_code": codes.most_common(1)[0][0] if codes else None,
            "top_code_count": codes.most_common(1)[0][1] if codes else 0,
        }


class CopyExecutionIntelligenceService:
    """Read-only observability over the paper execution decision funnel."""

    def report(self, telegram_id: int, *, days: int = 30, recent_limit: int = 8) -> dict[str, Any]:
        safe_days = max(1, min(int(days), 365))
        safe_limit = max(1, min(int(recent_limit), 25))
        since = (datetime.now(timezone.utc) - timedelta(days=safe_days)).isoformat()
        with connect() as conn:
            rejected_rows = [dict(row) for row in conn.execute(
                """SELECT signal_id,symbol,timeframe,side,rejection_code,rejection_reason,created_at
                   FROM paper_positions
                   WHERE telegram_id=? AND status='REJECTED' AND created_at>=?
                   ORDER BY id DESC""",
                (telegram_id, since),
            ).fetchall()]
            funnel_row = conn.execute(
                """SELECT
                     COUNT(*) total,
                     SUM(CASE WHEN status='REJECTED' THEN 1 ELSE 0 END) rejected,
                     SUM(CASE WHEN status IN ('OPEN','PARTIAL','CLOSED') THEN 1 ELSE 0 END) accepted
                   FROM paper_positions
                   WHERE telegram_id=? AND created_at>=?""",
                (telegram_id, since),
            ).fetchone()

        analytics = RejectionAnalytics.build(rejected_rows)
        total = int(funnel_row[0] or 0)
        rejected = int(funnel_row[1] or 0)
        accepted = int(funnel_row[2] or 0)
        analytics.update({
            "days": safe_days,
            "attempts": total,
            "accepted": accepted,
            "rejected": rejected,
            "acceptance_rate": (accepted / total * 100.0) if total else 0.0,
            "recent": rejected_rows[:safe_limit],
        })
        return analytics
