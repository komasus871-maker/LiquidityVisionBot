"""Read-only economic observability across scanner, research and PAPER domains.

The dashboard deliberately keeps simulated PAPER value, research evidence and
infrastructure expense separate.  It has no execution or promotion authority.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from database.database import connect
from services.copy_trading import CopyTradingService
from services.forward_runtime_state import ForwardRuntimeStateRepository
from services.pump_dump_scanner import ScannerRepository


def _optional_cost(name: str) -> float | None:
    value = os.getenv(name, "").strip()
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


class EconomicValueDashboard:
    """Bounded current-state dashboard; PAPER PnL is never represented as cash profit."""

    def report(self, telegram_id: int) -> dict[str, Any]:
        paper = CopyTradingService().performance_stats(telegram_id)
        scanner = ScannerRepository.home_stats(telegram_id=telegram_id)
        cohorts = {
            f"{horizon}m": ScannerRepository.outcome_attribution(
                horizon_minutes=horizon, minimum_samples=30,
            ) for horizon in (5, 15, 60, 240)
        }
        cutoff_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        with connect() as conn:
            hypothesis_rows = conn.execute(
                "SELECT lifecycle_state,COUNT(*) FROM research_hypotheses GROUP BY lifecycle_state"
            ).fetchall()
            episode_row = conn.execute(
                """SELECT COUNT(DISTINCT episode_id),
                   SUM(CASE WHEN phase IN ('BUILDUP','EARLY_ANOMALY') THEN 1 ELSE 0 END)
                   FROM market_anomaly_events WHERE telegram_id=? AND observed_at>=?""",
                (telegram_id, cutoff_24h),
            ).fetchone()
        hypothesis_states = {str(row[0]): int(row[1]) for row in hypothesis_rows}
        forward = ForwardRuntimeStateRepository().health() or {}
        storage = forward.get("storage") or {}
        remote_bytes = float(storage.get("current_30_day_stored_bytes") or 0)
        r2_rate = _optional_cost("INFRA_R2_STORAGE_USD_PER_GB_MONTH")
        r2_storage = (remote_bytes / (1024 ** 3) * r2_rate) if r2_rate is not None else None
        costs = {
            "render_compute_usd_month": _optional_cost("INFRA_RENDER_COMPUTE_USD_MONTH"),
            "render_disk_usd_month": _optional_cost("INFRA_RENDER_DISK_USD_MONTH"),
            "database_usd_month": _optional_cost("INFRA_DATABASE_USD_MONTH"),
            "r2_storage_usd_month": r2_storage,
            "r2_operations_usd_month": _optional_cost("INFRA_R2_OPERATIONS_USD_MONTH"),
        }
        known = [value for value in costs.values() if value is not None]
        complete = len(known) == len(costs)
        total = sum(known)
        paper_net = float(paper["actual_net_pnl"])
        return {
            "classification": "OPERATOR_ECONOMIC_DASHBOARD",
            "economic_authority": False,
            "paper_value_is_real_profit": False,
            "scanner": {
                **scanner,
                "episodes_24h": int(episode_row[0] or 0),
                "early_detection_events_24h": int(episode_row[1] or 0),
                "fixed_horizon_cohorts": cohorts,
            },
            "strategies": {
                "hypothesis_states": hypothesis_states,
                "validated_candidate_claim": False,
            },
            "paper": {
                "closed": paper["execution_closed"],
                "gross_pnl": paper["actual_gross_pnl"],
                "fees": paper["actual_fees"],
                "net_pnl_after_recorded_fees": paper_net,
                "expectancy_r": paper["strategy_expectancy_r"],
                "profit_factor": paper["strategy_profit_factor"],
                "drawdown_proxy_r": paper["strategy_drawdown_proxy_r"],
                "average_slippage_pct": paper["actual_average_slippage_pct"],
            },
            "infrastructure": {
                "costs": costs,
                "cost_status": "COMPLETE" if complete else "PARTIAL_UNCONFIGURED",
                "known_monthly_cost_usd": total,
                "estimated_total_monthly_cost_usd": total if complete else None,
            },
            "net_paper_value_after_infrastructure_usd": (
                paper_net - total if complete else None
            ),
            "net_value_status": "PAPER_SIMULATION_ONLY" if complete else "COST_INPUTS_INCOMPLETE",
        }
