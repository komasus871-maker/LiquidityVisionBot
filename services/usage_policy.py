from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from database.database import connect
from services.capabilities import CapabilityService


class UsagePolicyService:
    """Visible per-plan limits for expensive product paths; never grants execution authority."""

    def __init__(self) -> None:
        self.capabilities = CapabilityService()

    def consume(self, telegram_id: int, capability: str, limit_key: str,
                *, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        plan = self.capabilities.plan(telegram_id)["plan"]
        limit = int(self.capabilities.limits(telegram_id).get(limit_key, 0))
        day = datetime.now(timezone.utc).date().isoformat()
        with connect() as conn:
            row = conn.execute("""SELECT COUNT(*) AS n FROM feature_usage_events
                WHERE telegram_id=? AND capability=? AND outcome='ALLOWED' AND created_at>=?""",
                (telegram_id, capability, f"{day}T00:00:00+00:00")).fetchone()
            used = int(row["n"] if row else 0)
            allowed = limit > 0 and used < limit
            now = datetime.now(timezone.utc).isoformat()
            conn.execute("""INSERT INTO feature_usage_events(event_key,telegram_id,capability,plan_key,
                outcome,provider,estimated_cost_usd,metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                (str(uuid.uuid4()), telegram_id, capability, plan, "ALLOWED" if allowed else "LIMIT_REJECTED",
                 None, 0.0, json.dumps(metadata or {}, sort_keys=True), now))
        return {"allowed": allowed, "plan": plan, "limit": limit,
                "used": used + (1 if allowed else 0), "remaining": max(0, limit - used - (1 if allowed else 0))}

    def status(self, telegram_id: int) -> dict[str, Any]:
        plan = self.capabilities.plan(telegram_id)["plan"]
        limits = self.capabilities.limits(telegram_id)
        day = datetime.now(timezone.utc).date().isoformat()
        mapping = {
            "scanner": ("MARKET_SCANNER", "scanner_daily"),
            "ai": ("ADVANCED_AI", "advanced_ai_daily"),
            "research": ("ADVANCED_RESEARCH", "advanced_research_daily"),
            "exports": ("RESEARCH_EXPORT", "export_daily"),
            "alerts": ("INTELLIGENCE_ALERT", "alert_daily"),
        }
        with connect() as conn:
            rows = [dict(row) for row in conn.execute("""SELECT capability,COUNT(*) n
                FROM feature_usage_events WHERE telegram_id=? AND outcome='ALLOWED'
                AND created_at>=? GROUP BY capability""",
                (telegram_id, f"{day}T00:00:00+00:00")).fetchall()]
        counts = {str(row["capability"]): int(row["n"]) for row in rows}
        items = {}
        for name, (capability, limit_key) in mapping.items():
            limit = int(limits.get(limit_key, 0))
            used = counts.get(capability, 0)
            items[name] = {"used": used, "limit": limit, "remaining": max(0, limit - used)}
        return {"plan": plan, "items": items, "reset": "00:00 UTC",
                "execution_authority": False}
