from __future__ import annotations

import csv
import io
import json
from typing import Any

from services.paper_copy_analytics import PaperCopyAnalyticsService


class UserAnalyticsExportService:
    """Secret-free export of aggregate analytics scoped to one Telegram user."""

    def build(self, telegram_id: int, *, format_name: str, days: int = 90) -> tuple[str, bytes]:
        report = PaperCopyAnalyticsService().report(int(telegram_id), days=days)
        normalized = format_name.strip().lower()
        if normalized == "json":
            payload = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
            return f"liquidity_vision_analytics_{report['days']}d.json", payload
        if normalized != "csv":
            raise ValueError("EXPORT_FORMAT_NOT_SUPPORTED")
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=(
            "section", "key", "sample", "wins", "losses", "expectancy_r", "net_r",
            "avoided_losses", "missed_wins", "average_future_mfe_pct",
            "average_future_mae_pct", "average_modeled_cost_pct",
        ))
        writer.writeheader()
        for section in ("by_strategy", "by_timeframe", "by_symbol", "by_quality", "by_readiness"):
            for item in report.get(section) or []:
                writer.writerow({"section": section, **item})
        for code, item in (report.get("guardrail_counterfactuals") or {}).items():
            writer.writerow({
                "section": "guardrail_counterfactual", "key": code,
                "sample": item.get("sample"), "net_r": item.get("net_shadow_r"),
                "expectancy_r": item.get("average_shadow_r"),
                "avoided_losses": item.get("avoided_losses"), "missed_wins": item.get("missed_wins"),
                "average_future_mfe_pct": item.get("average_future_mfe_pct"),
                "average_future_mae_pct": item.get("average_future_mae_pct"),
                "average_modeled_cost_pct": item.get("average_modeled_cost_pct"),
            })
        return f"liquidity_vision_analytics_{report['days']}d.csv", output.getvalue().encode("utf-8-sig")

    @staticmethod
    def safety_contract() -> dict[str, Any]:
        return {"user_scoped": True, "contains_credentials": False,
                "contains_provider_secrets": False, "contains_hidden_ai_reasoning": False,
                "paper_live_mixed": False, "economic_authority": False}
