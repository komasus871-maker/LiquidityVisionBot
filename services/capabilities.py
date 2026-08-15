from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from database.database import connect


ENTITLEMENT_VERSION = "entitlements-v1"
PLAN_VERSION = "product-plans-v1"

CAPABILITIES: dict[str, dict[str, Any]] = {
    "BASIC_SIGNALS": {"mode": "CORE", "description": "Basic signals and lifecycle"},
    "BASIC_JOURNAL": {"mode": "CORE", "description": "Journal and basic performance"},
    "BASIC_PAPER_COPY": {"mode": "PAPER", "description": "Safe deterministic PAPER copy"},
    "MARKET_STORY_FULL": {"mode": "OBSERVE", "description": "Full Market Story"},
    "SIGNAL_QUALITY_FULL": {"mode": "OBSERVE", "description": "Signal Quality V3 decomposition"},
    "MICROSTRUCTURE_VIEW": {"mode": "OBSERVE", "description": "Bounded order-book intelligence"},
    "DERIVATIVES_VIEW": {"mode": "OBSERVE", "description": "Public funding and open interest"},
    "ADVANCED_RANKING": {"mode": "RESEARCH_ONLY", "description": "Signal Ranking V4"},
    "COPY_CUSTOM_PROFILE": {"mode": "PAPER", "description": "Custom PAPER copy profiles"},
    "COPY_ADVANCED_FILTERS": {"mode": "PAPER", "description": "Advanced deterministic copy filters"},
    "RESEARCH_STRATEGY_LAB": {"mode": "SHADOW", "description": "Strategy comparison lab"},
    "RESEARCH_EDGE_DISCOVERY": {"mode": "RESEARCH_ONLY", "description": "Edge discovery"},
    "RESEARCH_FORWARD_TESTS": {"mode": "RESEARCH_ONLY", "description": "Frozen forward tests"},
    "SCALPING_RESEARCH": {"mode": "PAPER_SHADOW", "description": "Cost-aware scalping research"},
    "AI_ADVANCED_COMMENTARY": {"mode": "ADVISORY", "description": "Advanced AI red-team commentary"},
    "EXPORT_RESEARCH_DATA": {"mode": "RESEARCH_ONLY", "description": "Research-safe export"},
    "PORTFOLIO_EDGE": {"mode": "RESEARCH_ONLY", "description": "Portfolio overlap research"},
    "ADVANCED_ALERTS": {"mode": "OBSERVE", "description": "Material intelligence alerts"},
    "PRIORITY_ANALYSIS": {"mode": "OBSERVE", "description": "Higher analysis usage allowance"},
    # Compatibility capabilities retained as aliases for earlier callers.
    "COPY_TRADING": {"mode": "PAPER", "description": "Automatic deterministic PAPER copy"},
    "ADVANCED_ANALYTICS": {"mode": "OBSERVE", "description": "Cohort and outcome analytics"},
    "AI_ANALYSIS": {"mode": "ADVISORY", "description": "Non-authoritative AI observation"},
    "STRATEGY_LAB": {"mode": "SHADOW", "description": "Legacy Strategy Lab alias"},
    "EDGE_DISCOVERY": {"mode": "RESEARCH_ONLY", "description": "Legacy edge discovery alias"},
    "FORWARD_VALIDATION": {"mode": "RESEARCH_ONLY", "description": "Legacy forward validation alias"},
    "ADVANCED_RISK_PROFILES": {"mode": "PAPER", "description": "Advanced PAPER risk profiles"},
}

PLAN_DEFINITIONS: dict[str, dict[str, Any]] = {
    "FREE": {
        "name": "Free", "description": "Useful core market intelligence and PAPER experience",
        "capabilities": {"BASIC_SIGNALS", "BASIC_JOURNAL", "BASIC_PAPER_COPY", "COPY_TRADING",
                         "STRATEGY_LAB"},
        "limits": {"watchlist_items": 20, "advanced_ai_daily": 0, "scanner_daily": 10},
    },
    "PRO": {
        "name": "Pro", "description": "Advanced market intelligence and customization",
        "inherits": "FREE",
        "capabilities": {"MARKET_STORY_FULL", "SIGNAL_QUALITY_FULL", "MICROSTRUCTURE_VIEW",
                         "DERIVATIVES_VIEW", "ADVANCED_RANKING", "COPY_CUSTOM_PROFILE",
                         "COPY_ADVANCED_FILTERS", "ADVANCED_ANALYTICS", "ADVANCED_RISK_PROFILES",
                         "ADVANCED_ALERTS", "PRIORITY_ANALYSIS"},
        "limits": {"watchlist_items": 100, "advanced_ai_daily": 10, "scanner_daily": 50},
    },
    "ELITE": {
        "name": "Elite Intelligence", "description": "Full research and red-team intelligence",
        "inherits": "PRO",
        "capabilities": {"RESEARCH_STRATEGY_LAB", "RESEARCH_EDGE_DISCOVERY",
                         "RESEARCH_FORWARD_TESTS", "SCALPING_RESEARCH", "AI_ADVANCED_COMMENTARY",
                         "EXPORT_RESEARCH_DATA", "PORTFOLIO_EDGE", "STRATEGY_LAB", "EDGE_DISCOVERY",
                         "FORWARD_VALIDATION", "AI_ANALYSIS"},
        "limits": {"watchlist_items": 250, "advanced_ai_daily": 30, "scanner_daily": 200},
    },
}

ALIASES = {"PREMIUM": "PRO", "INTELLIGENCE": "ELITE"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CapabilityService:
    """Central product boundary with no execution-policy authority."""

    @staticmethod
    def _normalize_plan(plan: str | None) -> str:
        value = str(plan or "FREE").strip().upper()
        value = ALIASES.get(value, value)
        if value not in PLAN_DEFINITIONS:
            raise ValueError(f"Unknown plan: {value}")
        return value

    @staticmethod
    def _plan_capabilities(plan: str) -> set[str]:
        result: set[str] = set()
        current: str | None = plan
        while current:
            definition = PLAN_DEFINITIONS[current]
            result.update(definition.get("capabilities") or set())
            current = definition.get("inherits")
        return result

    def plan(self, telegram_id: int) -> dict[str, Any]:
        now = _now()
        with connect() as conn:
            assignment = conn.execute("""SELECT * FROM user_plan_assignments WHERE telegram_id=?
                AND (expires_at IS NULL OR expires_at>?) ORDER BY updated_at DESC LIMIT 1""",
                (telegram_id, now.isoformat())).fetchone()
            legacy = conn.execute("SELECT premium,premium_tier,premium_until FROM users WHERE telegram_id=?",
                                  (telegram_id,)).fetchone()
        if assignment:
            row = dict(assignment)
            plan = self._normalize_plan(row.get("plan_key"))
            source = str(row.get("source") or "ASSIGNMENT")
            expires_at = row.get("expires_at")
        else:
            active_legacy = False
            if legacy and legacy[0]:
                try:
                    active_legacy = not legacy[2] or datetime.fromisoformat(str(legacy[2]).replace("Z", "+00:00")) > now
                except ValueError:
                    active_legacy = False
            plan = self._normalize_plan(legacy[1] if active_legacy and legacy else "FREE")
            source = "LEGACY_PREMIUM" if active_legacy else "PRODUCT_DEFAULT"
            expires_at = legacy[2] if active_legacy and legacy else None
        return {"plan": plan, "definition": PLAN_DEFINITIONS[plan], "source": source,
                "expires_at": expires_at, "version": PLAN_VERSION,
                "economic_authority": False}

    def has(self, telegram_id: int, capability: str) -> bool:
        key = str(capability or "").strip().upper()
        if key not in CAPABILITIES:
            return False
        now = _now().isoformat()
        with connect() as conn:
            override = conn.execute("""SELECT enabled FROM capability_entitlements
                WHERE telegram_id=? AND capability=? AND (expires_at IS NULL OR expires_at>?)""",
                (telegram_id, key, now)).fetchone()
        if override is not None:
            return bool(override[0])
        return key in self._plan_capabilities(self.plan(telegram_id)["plan"])

    def snapshot(self, telegram_id: int) -> dict[str, dict[str, Any]]:
        plan = self.plan(telegram_id)
        return {name: {**definition, "available": True, "enabled": self.has(telegram_id, name),
                       "source": plan["source"], "plan": plan["plan"],
                       "entitlement_version": ENTITLEMENT_VERSION,
                       "expires_at": plan["expires_at"], "economic_authority": False}
                for name, definition in CAPABILITIES.items()}

    def limits(self, telegram_id: int) -> dict[str, int]:
        plan = self.plan(telegram_id)["plan"]
        result: dict[str, int] = {}
        chain = []
        current: str | None = plan
        while current:
            chain.append(current)
            current = PLAN_DEFINITIONS[current].get("inherits")
        for key in reversed(chain):
            result.update(PLAN_DEFINITIONS[key].get("limits") or {})
        return result

    def required_plan(self, capability: str) -> str:
        key = str(capability or "").strip().upper()
        for plan in ("FREE", "PRO", "ELITE"):
            if key in self._plan_capabilities(plan):
                return plan
        return "OPERATOR"

    def preview(self, capability: str) -> str:
        key = str(capability or "").strip().upper()
        definition = CAPABILITIES.get(key) or {"description": key.replace("_", " ").title()}
        return (f"{definition['description']} is available with {self.required_plan(key)}. "
                "Use /plans to compare tiers. Plans never grant trading authority.")

    def set_entitlement(self, telegram_id: int, capability: str, *, enabled: bool,
                        source: str, expires_at: str | None = None,
                        actor_telegram_id: int | None = None,
                        audit_metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        key = str(capability or "").strip().upper()
        if key not in CAPABILITIES:
            raise ValueError(f"Unknown capability: {key}")
        now = _now().isoformat()
        with connect() as conn:
            conn.execute("""INSERT INTO capability_entitlements(telegram_id,capability,enabled,source,expires_at,updated_at)
                VALUES(?,?,?,?,?,?) ON CONFLICT(telegram_id,capability) DO UPDATE SET
                enabled=excluded.enabled,source=excluded.source,expires_at=excluded.expires_at,
                updated_at=excluded.updated_at""",
                (telegram_id, key, int(enabled), str(source or "OPERATOR"), expires_at, now))
            conn.execute("""INSERT INTO entitlement_audit_events(telegram_id,actor_telegram_id,event_type,
                plan_key,capability,source,metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?)""",
                (telegram_id, actor_telegram_id, "CAPABILITY_OVERRIDE", None, key, source,
                 json.dumps(audit_metadata or {}, sort_keys=True), now))
        result = self.snapshot(telegram_id)[key]
        result["source"] = str(source or "OPERATOR")
        return result

    def assign_plan(self, telegram_id: int, plan: str, *, source: str,
                    actor_telegram_id: int | None = None, duration_days: int | None = None,
                    audit_metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        key = self._normalize_plan(plan)
        now = _now()
        expires = ((now + timedelta(days=max(1, int(duration_days)))).isoformat()
                   if duration_days is not None else None)
        with connect() as conn:
            conn.execute("""INSERT INTO user_plan_assignments(telegram_id,plan_key,plan_version,source,
                granted_at,expires_at,override,audit_metadata_json,updated_at) VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(telegram_id) DO UPDATE SET plan_key=excluded.plan_key,
                plan_version=excluded.plan_version,source=excluded.source,granted_at=excluded.granted_at,
                expires_at=excluded.expires_at,override=excluded.override,
                audit_metadata_json=excluded.audit_metadata_json,updated_at=excluded.updated_at""",
                (telegram_id, key, PLAN_VERSION, source, now.isoformat(), expires, 1,
                 json.dumps(audit_metadata or {}, sort_keys=True), now.isoformat()))
            conn.execute("""INSERT INTO entitlement_audit_events(telegram_id,actor_telegram_id,event_type,
                plan_key,capability,source,metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?)""",
                (telegram_id, actor_telegram_id, "PLAN_GRANTED", key, None, source,
                 json.dumps({"duration_days": duration_days, **(audit_metadata or {})}, sort_keys=True),
                 now.isoformat()))
            conn.execute("UPDATE users SET premium=?,premium_tier=?,premium_until=? WHERE telegram_id=?",
                         (int(key != "FREE"), key, expires, telegram_id))
        return self.plan(telegram_id)

    def revoke_plan(self, telegram_id: int, *, source: str,
                    actor_telegram_id: int | None = None) -> dict[str, Any]:
        now = _now().isoformat()
        with connect() as conn:
            conn.execute("DELETE FROM user_plan_assignments WHERE telegram_id=?", (telegram_id,))
            conn.execute("UPDATE users SET premium=0,premium_tier='FREE',premium_until=NULL WHERE telegram_id=?",
                         (telegram_id,))
            conn.execute("""INSERT INTO entitlement_audit_events(telegram_id,actor_telegram_id,event_type,
                plan_key,capability,source,metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?)""",
                (telegram_id, actor_telegram_id, "PLAN_REVOKED", "FREE", None, source, "{}", now))
        return self.plan(telegram_id)
