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
                rs.strategy_key,mi.full_snapshot_json,s.entry AS theoretical_entry,
                s.max_profit_pct AS future_mfe_pct,s.max_drawdown_pct AS future_mae_pct
                FROM paper_positions p
                LEFT JOIN signals s ON s.id=p.signal_id
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
            quality = intelligence.get("signal_quality_v4") or intelligence.get("signal_quality_v3") or intelligence.get("signal_quality_v2") or {}
            readiness = intelligence.get("entry_readiness") or {}
            fusion = intelligence.get("strategy_fusion_v2") or {}
            regime = intelligence.get("market_regime_v2") or {}
            microstructure = intelligence.get("microstructure") or {}
            liquidity = intelligence.get("liquidity_map") or intelligence.get("liquidity") or {}
            research = intelligence.get("research_policies") or {}
            strategy = row.get("strategy_key") or (fusion.get("primary") or {}).get("strategy") or "UNKNOWN"
            accepted = row.get("status") == "CLOSED"
            normalized.append({**row, "strategy": strategy,
                               "quality_bucket": _bucket(quality.get("overall_quality")),
                               "readiness_bucket": _bucket(readiness.get("score")),
                               "regime": regime.get("phase") or "UNKNOWN",
                               "volatility": regime.get("volatility") or "UNKNOWN",
                               "liquidity": (microstructure.get("status") or
                                             ("MAPPED" if liquidity.get("unresolved_count") is not None else "UNKNOWN")),
                               "modeled_cost_pct": research.get("estimated_roundtrip_cost_pct"),
                               "outcome_r": float(row.get("realized_r") or 0) if accepted
                               else float(row.get("shadow_realized_r") or 0),
                               "outcome_kind": "EXECUTED" if accepted else "REJECTED_COUNTERFACTUAL"})
        counterfactuals = {}
        for code in sorted(COUNTERFACTUAL_CODES):
            selected = [item for item in normalized if item.get("rejection_code") == code]
            values = [float(item["outcome_r"]) for item in selected]
            costs = [float(item["modeled_cost_pct"]) for item in selected
                     if item.get("modeled_cost_pct") is not None]
            mfe = [float(item["future_mfe_pct"]) for item in selected
                   if item.get("future_mfe_pct") is not None]
            mae = [float(item["future_mae_pct"]) for item in selected
                   if item.get("future_mae_pct") is not None]
            counterfactuals[code] = {
                "sample": len(values), "avoided_losses": sum(value < 0 for value in values),
                "missed_wins": sum(value > 0 for value in values),
                "net_shadow_r": round(sum(values), 4),
                "average_shadow_r": round(sum(values) / len(values), 4) if values else 0.0,
                "average_future_mfe_pct": round(sum(mfe) / len(mfe), 4) if mfe else None,
                "average_future_mae_pct": round(sum(mae) / len(mae), 4) if mae else None,
                "average_modeled_cost_pct": round(sum(costs) / len(costs), 6) if costs else None,
                "by_symbol": _groups(selected, "symbol"),
                "by_timeframe": _groups(selected, "timeframe"),
                "by_strategy": _groups(selected, "strategy"),
                "by_regime": _groups(selected, "regime"),
                "by_liquidity": _groups(selected, "liquidity"),
                "by_volatility": _groups(selected, "volatility"),
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
