from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from database.database import connect


CAPABILITIES: dict[str, dict[str, Any]] = {
    "COPY_TRADING": {"available": True, "mode": "PAPER", "description": "Automatic deterministic PAPER copy"},
    "ADVANCED_ANALYTICS": {"available": True, "mode": "OBSERVE", "description": "Cohort and outcome analytics"},
    "AI_ANALYSIS": {"available": True, "mode": "ADVISORY", "description": "Non-authoritative AI observation"},
    "STRATEGY_LAB": {"available": True, "mode": "SHADOW", "description": "Non-trading strategy comparison"},
    "EDGE_DISCOVERY": {"available": True, "mode": "RESEARCH_ONLY", "description": "Leakage-controlled descriptive edge discovery"},
    "FORWARD_VALIDATION": {"available": True, "mode": "RESEARCH_ONLY", "description": "Frozen hypothesis forward cohorts"},
    "SCALPING_RESEARCH": {"available": True, "mode": "PAPER_SHADOW", "description": "After-cost 1m/3m/5m research"},
    "ADVANCED_RISK_PROFILES": {"available": True, "mode": "PAPER", "description": "Configurable deterministic PAPER risk"},
}


class CapabilityService:
    """Central product boundary; deliberately not an execution-policy authority."""

    def snapshot(self, telegram_id: int) -> dict[str, dict[str, Any]]:
        now = datetime.now(timezone.utc).isoformat()
        with connect() as conn:
            rows = conn.execute(
                """SELECT capability,enabled,source,expires_at FROM capability_entitlements
                   WHERE telegram_id=? AND (expires_at IS NULL OR expires_at>?)""",
                (telegram_id, now),
            ).fetchall()
        overrides = {str(row["capability"]).upper(): dict(row) for row in rows}
        result: dict[str, dict[str, Any]] = {}
        for name, definition in CAPABILITIES.items():
            override = overrides.get(name)
            result[name] = {
                **definition,
                "enabled": bool(override["enabled"]) if override else bool(definition["available"]),
                "source": str(override["source"]) if override else "PRODUCT_DEFAULT",
                "expires_at": override["expires_at"] if override else None,
                "economic_authority": False,
            }
        return result

    def set_entitlement(
        self, telegram_id: int, capability: str, *, enabled: bool,
        source: str, expires_at: str | None = None,
    ) -> dict[str, Any]:
        key = str(capability or "").strip().upper()
        if key not in CAPABILITIES:
            raise ValueError(f"Unknown capability: {key}")
        now = datetime.now(timezone.utc).isoformat()
        with connect() as conn:
            conn.execute(
                """INSERT INTO capability_entitlements(telegram_id,capability,enabled,source,expires_at,updated_at)
                   VALUES(?,?,?,?,?,?) ON CONFLICT(telegram_id,capability) DO UPDATE SET
                   enabled=excluded.enabled,source=excluded.source,expires_at=excluded.expires_at,
                   updated_at=excluded.updated_at""",
                (telegram_id, key, int(enabled), str(source or "OPERATOR"), expires_at, now),
            )
        return self.snapshot(telegram_id)[key]
