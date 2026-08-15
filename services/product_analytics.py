from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from database.database import connect
from services.capabilities import CapabilityService


class ProductAnalyticsService:
    """Privacy-conscious command and feature aggregates; stores no message bodies."""

    def record(self, telegram_id: int | None, capability: str, *, outcome: str = "USED",
               metadata: dict[str, Any] | None = None) -> None:
        plan = CapabilityService().plan(telegram_id)["plan"] if telegram_id is not None else "ANONYMOUS"
        with connect() as conn:
            conn.execute("""INSERT INTO feature_usage_events(event_key,telegram_id,capability,plan_key,
                outcome,provider,estimated_cost_usd,metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                (str(uuid.uuid4()), telegram_id, capability[:100], plan, outcome[:40], None,
                 0.0, json.dumps(metadata or {}, sort_keys=True),
                 datetime.now(timezone.utc).isoformat()))

    def usage(self, days: int = 1) -> dict[str, Any]:
        safe_days = max(1, min(90, int(days)))
        since = (datetime.now(timezone.utc) - timedelta(days=safe_days)).isoformat()
        with connect() as conn:
            users = conn.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
            active = conn.execute("""SELECT COUNT(DISTINCT telegram_id) n FROM feature_usage_events
                WHERE created_at>=? AND telegram_id IS NOT NULL""", (since,)).fetchone()["n"]
            plans = [dict(row) for row in conn.execute("""SELECT plan_key,COUNT(*) n
                FROM user_plan_assignments WHERE expires_at IS NULL OR expires_at>?
                GROUP BY plan_key ORDER BY n DESC""", (datetime.now(timezone.utc).isoformat(),)).fetchall()]
            commands = [dict(row) for row in conn.execute("""SELECT capability,COUNT(*) n
                FROM feature_usage_events WHERE created_at>=? AND capability LIKE 'COMMAND:%'
                GROUP BY capability ORDER BY n DESC LIMIT 10""", (since,)).fetchall()]
            outcomes = [dict(row) for row in conn.execute("""SELECT outcome,COUNT(*) n
                FROM feature_usage_events WHERE created_at>=? GROUP BY outcome ORDER BY n DESC""",
                (since,)).fetchall()]
            alerts = [dict(row) for row in conn.execute("""SELECT status,COUNT(*) n
                FROM intelligence_alert_events WHERE created_at>=? GROUP BY status ORDER BY n DESC""",
                (since,)).fetchall()]
        assigned = sum(int(row["n"]) for row in plans)
        plan_distribution = {row["plan_key"]: int(row["n"]) for row in plans}
        plan_distribution["FREE_DEFAULT"] = max(0, int(users) - assigned)
        return {"days": safe_days, "registered_users": int(users), "active_users": int(active),
                "plans": plan_distribution, "commands": commands, "outcomes": outcomes,
                "alerts": alerts, "private_content_stored": False}

    def ai_usage(self, days: int = 1) -> dict[str, Any]:
        safe_days = max(1, min(90, int(days)))
        since = (datetime.now(timezone.utc) - timedelta(days=safe_days)).isoformat()
        with connect() as conn:
            summary = dict(conn.execute("""SELECT COUNT(*) decisions,
                COALESCE(SUM(estimated_cost_usd),0) cost,
                SUM(CASE WHEN cache_hit=1 THEN 1 ELSE 0 END) reused,
                COALESCE(SUM(estimated_cost_avoided_usd),0) avoided_cost,
                SUM(CASE WHEN provider_invoked=0 THEN 1 ELSE 0 END) calls_avoided,
                SUM(CASE WHEN validation_code<>'VALID' THEN 1 ELSE 0 END) failures
                FROM ai_decisions WHERE created_at>=?""", (since,)).fetchone())
            users = [dict(row) for row in conn.execute("""SELECT telegram_id,COUNT(*) decisions,
                COALESCE(SUM(estimated_cost_usd),0) cost FROM ai_decisions WHERE created_at>=?
                GROUP BY telegram_id ORDER BY cost DESC LIMIT 10""", (since,)).fetchall()]
            providers = [dict(row) for row in conn.execute("""SELECT provider,model,COUNT(*) decisions,
                COALESCE(SUM(estimated_cost_usd),0) cost FROM ai_decisions WHERE created_at>=?
                GROUP BY provider,model ORDER BY cost DESC""", (since,)).fetchall()]
        return {"days": safe_days, "summary": summary, "top_users": users,
                "providers": providers, "user_content_exposed": False}
