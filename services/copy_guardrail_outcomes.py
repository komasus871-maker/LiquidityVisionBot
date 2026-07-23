from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from database.database import connect


@dataclass(frozen=True)
class GuardrailOutcome:
    code: str
    resolved: int
    avoided_losses: int
    missed_wins: int
    net_shadow_r: float
    average_shadow_r: float


class GuardrailOutcomeAnalytics:
    """Pure aggregation for counterfactual outcomes of rejected paper executions."""

    @staticmethod
    def build(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
        records = list(rows)
        grouped: dict[str, list[float]] = defaultdict(list)
        for row in records:
            code = str(row.get("rejection_code") or "UNKNOWN")
            grouped[code].append(float(row.get("shadow_realized_r") or 0.0))

        outcomes: list[GuardrailOutcome] = []
        for code, values in grouped.items():
            total_r = sum(values)
            outcomes.append(GuardrailOutcome(
                code=code,
                resolved=len(values),
                avoided_losses=sum(value < 0 for value in values),
                missed_wins=sum(value > 0 for value in values),
                net_shadow_r=total_r,
                average_shadow_r=total_r / len(values) if values else 0.0,
            ))
        outcomes.sort(key=lambda item: (-item.resolved, item.code))
        total_r = sum(float(row.get("shadow_realized_r") or 0.0) for row in records)
        return {
            "resolved": len(records),
            "avoided_losses": sum(float(row.get("shadow_realized_r") or 0.0) < 0 for row in records),
            "missed_wins": sum(float(row.get("shadow_realized_r") or 0.0) > 0 for row in records),
            "net_shadow_r": total_r,
            "average_shadow_r": total_r / len(records) if records else 0.0,
            "by_code": outcomes,
        }


class CopyGuardrailOutcomeService:
    """Read-only report over resolved counterfactual outcomes of rejected signals."""

    def report(self, telegram_id: int, *, days: int = 30, recent_limit: int = 8) -> dict[str, Any]:
        safe_days = max(1, min(int(days), 365))
        safe_limit = max(1, min(int(recent_limit), 25))
        since = (datetime.now(timezone.utc) - timedelta(days=safe_days)).isoformat()
        with connect() as conn:
            rows = [dict(row) for row in conn.execute(
                """SELECT signal_id,symbol,timeframe,side,rejection_code,shadow_realized_r,
                          shadow_result,shadow_exit_price,shadow_closed_at
                   FROM paper_positions
                   WHERE telegram_id=? AND status='REJECTED' AND shadow_closed_at IS NOT NULL
                     AND created_at>=?
                   ORDER BY shadow_closed_at DESC""",
                (telegram_id, since),
            ).fetchall()]
        result = GuardrailOutcomeAnalytics.build(rows)
        result.update({"days": safe_days, "recent": rows[:safe_limit]})
        return result
