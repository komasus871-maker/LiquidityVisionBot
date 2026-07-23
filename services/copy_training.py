from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Any

from database.database import connect


@dataclass(frozen=True)
class CopyTrainingPolicy:
    sample_size: int = 0
    win_rate: float = 0.0
    average_r: float = 0.0
    expectancy_r: float = 0.0
    confidence_adjustment: float = 0.0
    risk_multiplier: float = 1.0
    blocked: bool = False
    code: str = "INSUFFICIENT_DATA"
    reason: str = "Not enough closed paper executions for adaptive policy"


class CopyTrainingService:
    """Leakage-safe learning layer built exclusively from closed paper executions.

    The service deliberately uses conservative Bayesian shrinkage and a minimum
    sample threshold. It never learns from open positions, rejected entries or
    future signal state, which keeps execution policy deterministic and auditable.
    """

    MIN_SAMPLE = 8
    BLOCK_SAMPLE = 15
    PRIOR_TRADES = 12.0
    PRIOR_WIN_RATE = 0.50
    PRIOR_EXPECTANCY_R = 0.0

    def policy_for(self, telegram_id: int, signal: dict[str, Any]) -> CopyTrainingPolicy:
        rows = self._closed_rows(telegram_id, signal)
        sample_size = len(rows)
        if sample_size < self.MIN_SAMPLE:
            return CopyTrainingPolicy(sample_size=sample_size)

        realized = [float(row.get("realized_r") or 0.0) for row in rows]
        wins = sum(1 for value in realized if value > 0)
        raw_win_rate = wins / sample_size
        raw_expectancy = sum(realized) / sample_size

        denominator = sample_size + self.PRIOR_TRADES
        win_rate = (wins + self.PRIOR_WIN_RATE * self.PRIOR_TRADES) / denominator
        expectancy = (
            sum(realized) + self.PRIOR_EXPECTANCY_R * self.PRIOR_TRADES
        ) / denominator
        average_r = raw_expectancy

        # Confidence increases slowly and penalties arrive faster. The bounded
        # adjustment protects users from a small hot streak or one bad cluster.
        confidence_adjustment = max(-15.0, min(8.0, expectancy * 12.0))
        risk_multiplier = max(0.25, min(1.25, 1.0 + expectancy * 0.35))

        # Conservative lower confidence bound for positive expectancy. This is
        # intentionally simple and deterministic so it is identical on SQLite
        # and PostgreSQL and easy to reconstruct from the ledger.
        stderr = 1.0 / sqrt(max(sample_size, 1))
        lower_bound = expectancy - 1.28 * stderr
        blocked = sample_size >= self.BLOCK_SAMPLE and lower_bound < -0.35
        code = "NEGATIVE_COHORT_EDGE" if blocked else "ADAPTIVE_POLICY"
        reason = (
            f"Historical cohort remains negative after {sample_size} closed executions"
            if blocked
            else f"Adaptive policy from {sample_size} closed executions"
        )
        return CopyTrainingPolicy(
            sample_size=sample_size,
            win_rate=win_rate * 100.0,
            average_r=average_r,
            expectancy_r=expectancy,
            confidence_adjustment=confidence_adjustment,
            risk_multiplier=risk_multiplier,
            blocked=blocked,
            code=code,
            reason=reason,
        )

    def report(self, telegram_id: int) -> dict[str, Any]:
        with connect() as conn:
            rows = conn.execute(
                """SELECT p.symbol,p.timeframe,p.side,s.setup_key,p.realized_r,p.realized_pnl
                   FROM paper_positions p
                   LEFT JOIN signals s ON s.id=p.signal_id
                   WHERE p.telegram_id=? AND p.status='CLOSED'
                   ORDER BY p.closed_at DESC,p.id DESC""",
                (telegram_id,),
            ).fetchall()
        values = [dict(row) for row in rows]
        realized = [float(row.get("realized_r") or 0.0) for row in values]
        wins = sum(1 for value in realized if value > 0)
        losses = sum(1 for value in realized if value < 0)
        total = len(realized)

        cohorts: dict[str, list[float]] = {}
        for row in values:
            key = self._cohort_key(row)
            cohorts.setdefault(key, []).append(float(row.get("realized_r") or 0.0))
        ranked = sorted(
            (
                {
                    "cohort": key,
                    "sample_size": len(items),
                    "average_r": sum(items) / len(items),
                    "win_rate": sum(1 for value in items if value > 0) / len(items) * 100.0,
                }
                for key, items in cohorts.items()
                if len(items) >= 3
            ),
            key=lambda item: (item["average_r"], item["sample_size"]),
            reverse=True,
        )
        return {
            "sample_size": total,
            "wins": wins,
            "losses": losses,
            "win_rate": wins / total * 100.0 if total else 0.0,
            "average_r": sum(realized) / total if total else 0.0,
            "total_r": sum(realized),
            "best_cohorts": ranked[:3],
            "weakest_cohorts": list(reversed(ranked[-3:])),
            "learning_ready": total >= self.MIN_SAMPLE,
        }

    def _closed_rows(self, telegram_id: int, signal: dict[str, Any]) -> list[dict[str, Any]]:
        symbol = str(signal.get("symbol") or "").upper()
        timeframe = str(signal.get("timeframe") or "").lower()
        side = str(signal.get("side") or "").upper()
        setup_key = str(signal.get("setup_key") or "")
        with connect() as conn:
            rows = conn.execute(
                """SELECT p.realized_r,p.symbol,p.timeframe,p.side,s.setup_key
                   FROM paper_positions p
                   LEFT JOIN signals s ON s.id=p.signal_id
                   WHERE p.telegram_id=? AND p.status='CLOSED'
                     AND p.side=?
                     AND (p.symbol=? OR p.timeframe=? OR COALESCE(s.setup_key,'')=?)
                   ORDER BY p.closed_at DESC,p.id DESC LIMIT 120""",
                (telegram_id, side, symbol, timeframe, setup_key),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _cohort_key(row: dict[str, Any]) -> str:
        setup = str(row.get("setup_key") or "unknown")
        return f"{str(row.get('side') or '?').upper()} · {str(row.get('timeframe') or '?').upper()} · {setup}"
