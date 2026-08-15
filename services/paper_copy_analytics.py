from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from database.database import connect


COUNTERFACTUAL_CODES = frozenset({"MAX_SLIPPAGE", "MAX_HEAT", "LOW_CONFIDENCE"})


def _bucket(value: Any) -> str:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return "UNKNOWN"
    return "80-100" if score >= 80 else "65-79" if score >= 65 else "50-64" if score >= 50 else "0-49"


def _groups(rows: Iterable[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(key) or "UNKNOWN")].append(float(row.get("outcome_r") or 0))
    result = []
    for name, values in grouped.items():
        result.append({"key": name, "sample": len(values),
                       "wins": sum(value > 0 for value in values),
                       "losses": sum(value < 0 for value in values),
                       "expectancy_r": round(sum(values) / len(values), 4),
                       "net_r": round(sum(values), 4)})
    return sorted(result, key=lambda item: (-item["sample"], item["key"]))


class PaperCopyAnalyticsService:
    """Decision-time PAPER cohorts and explicit guardrail counterfactuals."""

    def report(self, telegram_id: int, *, days: int = 90) -> dict[str, Any]:
        safe_days = max(1, min(int(days), 365))
        since = (datetime.now(timezone.utc) - timedelta(days=safe_days)).isoformat()
        with connect() as conn:
            rows = [dict(row) for row in conn.execute("""SELECT p.signal_id,p.symbol,p.timeframe,p.status,
                p.realized_r,p.rejection_code,p.shadow_realized_r,p.closed_at,p.shadow_closed_at,
                rs.strategy_key,mi.full_snapshot_json
                FROM paper_positions p
                LEFT JOIN research_signal_snapshots rs ON rs.signal_id=p.signal_id
                LEFT JOIN market_intelligence_snapshots mi ON mi.id=(
                    SELECT MAX(mi2.id) FROM market_intelligence_snapshots mi2 WHERE mi2.signal_id=p.signal_id)
                WHERE p.telegram_id=? AND p.created_at>=?
                AND ((p.status='CLOSED' AND p.closed_at IS NOT NULL)
                  OR (p.status='REJECTED' AND p.shadow_closed_at IS NOT NULL))
                ORDER BY p.id DESC""", (telegram_id, since)).fetchall()]
        normalized = []
        for row in rows:
            try:
                intelligence = json.loads(row.get("full_snapshot_json") or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                intelligence = {}
            quality = intelligence.get("signal_quality_v3") or intelligence.get("signal_quality_v2") or {}
            readiness = intelligence.get("entry_readiness") or {}
            fusion = intelligence.get("strategy_fusion_v2") or {}
            strategy = row.get("strategy_key") or (fusion.get("primary") or {}).get("strategy") or "UNKNOWN"
            accepted = row.get("status") == "CLOSED"
            normalized.append({**row, "strategy": strategy,
                               "quality_bucket": _bucket(quality.get("overall_quality")),
                               "readiness_bucket": _bucket(readiness.get("score")),
                               "outcome_r": float(row.get("realized_r") or 0) if accepted
                               else float(row.get("shadow_realized_r") or 0),
                               "outcome_kind": "EXECUTED" if accepted else "REJECTED_COUNTERFACTUAL"})
        counterfactuals = {}
        for code in sorted(COUNTERFACTUAL_CODES):
            selected = [item for item in normalized if item.get("rejection_code") == code]
            values = [float(item["outcome_r"]) for item in selected]
            counterfactuals[code] = {
                "sample": len(values), "avoided_losses": sum(value < 0 for value in values),
                "missed_wins": sum(value > 0 for value in values),
                "net_shadow_r": round(sum(values), 4),
                "average_shadow_r": round(sum(values) / len(values), 4) if values else 0.0,
                "policy_change_authority": False,
            }
        return {"version": "paper-copy-analytics-v2", "days": safe_days,
                "resolved": len(normalized),
                "executed": sum(item["outcome_kind"] == "EXECUTED" for item in normalized),
                "counterfactual": sum(item["outcome_kind"] == "REJECTED_COUNTERFACTUAL" for item in normalized),
                "by_strategy": _groups(normalized, "strategy"),
                "by_timeframe": _groups(normalized, "timeframe"),
                "by_symbol": _groups(normalized, "symbol"),
                "by_quality": _groups(normalized, "quality_bucket"),
                "by_readiness": _groups(normalized, "readiness_bucket"),
                "guardrail_counterfactuals": counterfactuals,
                "future_data_in_decision_features": False,
                "economic_authority": False, "execution_authority": False}
