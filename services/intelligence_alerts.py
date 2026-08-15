from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from database.database import connect
from services.capabilities import CapabilityService
from services.user_preferences import UserPreferenceService
from services.usage_policy import UsagePolicyService
from utils.symbols import normalize_usdt_symbol


ALERT_CAPABILITY = {
    "ORDER_BOOK_WALL_APPEARS": "ADVANCED_ALERTS", "WALL_REMOVED": "ADVANCED_ALERTS",
    "WALL_REPLENISHED": "ADVANCED_ALERTS", "OI_ACCELERATION": "ADVANCED_ALERTS",
    "FUNDING_EXTREME": "ADVANCED_ALERTS", "LIQUIDITY_SWEEP": "ADVANCED_ALERTS",
    "QUALITY_CHANGE": "ADVANCED_ALERTS", "MICROSTRUCTURE_CHANGE": "ADVANCED_ALERTS",
    "STRATEGY_CHANGE": "ADVANCED_ALERTS", "REGIME_CHANGE": "ADVANCED_ALERTS",
    "PROVIDER_DEGRADATION": "ADVANCED_ALERTS",
}

ALERT_CATEGORY = {
    "STATUS_CHANGE": "TRADE_LIFECYCLE", "DIRECTION_CHANGE": "MATERIAL_INTELLIGENCE",
    "READINESS_CHANGE": "MATERIAL_INTELLIGENCE", "ENTRY_ZONE": "TRADE_LIFECYCLE",
    "STRUCTURE_BREAK": "LIQUIDITY", "LIQUIDITY_SWEEP": "LIQUIDITY",
    "QUALITY_CHANGE": "QUALITY", "STRATEGY_CHANGE": "RANKING",
    "REGIME_CHANGE": "MARKET", "MICROSTRUCTURE_CHANGE": "MICROSTRUCTURE",
    "ORDER_BOOK_WALL_APPEARS": "MICROSTRUCTURE", "WALL_REMOVED": "MICROSTRUCTURE",
    "WALL_REPLENISHED": "MICROSTRUCTURE", "OI_ACCELERATION": "MARKET",
    "FUNDING_EXTREME": "MARKET",
    "TRADE_ACTIVATION": "TRADE_LIFECYCLE", "TAKE_PROFIT": "TRADE_LIFECYCLE",
    "STOP": "TRADE_LIFECYCLE", "INVALIDATION": "TRADE_LIFECYCLE",
    "PROVIDER_DEGRADATION": "SYSTEM", "LIVE_RISK_EVENT": "LIVE_SAFETY",
    "RECONCILIATION_MISMATCH": "LIVE_SAFETY",
}

CRITICAL_LIVE_TYPES = frozenset({"LIVE_RISK_EVENT", "RECONCILIATION_MISMATCH"})
IDENTITY_SENSITIVE_TYPES = frozenset({
    "TRADE_ACTIVATION", "TAKE_PROFIT", "STOP", "INVALIDATION", *CRITICAL_LIVE_TYPES,
})


class IntelligenceAlertService:
    """Persist-first, entitlement-aware, debounced alert eligibility engine."""

    def __init__(self, debounce_minutes: int = 30) -> None:
        self.debounce_minutes = max(1, int(debounce_minutes))
        self.capabilities = CapabilityService()
        self.preferences = UserPreferenceService()
        self.usage = UsagePolicyService()

    def evaluate(self, telegram_id: int, *, symbol: str, timeframe: str, alert_type: str,
                 state_identity: str, severity: str = "INFO",
                 details: dict[str, Any] | None = None, occurred_at: datetime | None = None) -> dict[str, Any]:
        canonical = normalize_usdt_symbol(symbol)
        kind = str(alert_type).strip().upper()
        occurred = occurred_at or datetime.now(timezone.utc)
        capability = ALERT_CAPABILITY.get(kind)
        critical_live = severity.strip().upper() == "CRITICAL" and kind in CRITICAL_LIVE_TYPES
        categories = set(self.preferences.get(telegram_id)["notification_categories"])
        required_category = ALERT_CATEGORY.get(kind)
        status, reason = "ELIGIBLE", None
        with connect() as conn:
            user = conn.execute("SELECT notifications_enabled FROM users WHERE telegram_id=?",
                                (telegram_id,)).fetchone()
        notifications_enabled = user is None or bool(user[0])
        if (not notifications_enabled or not categories) and not critical_live:
            status, reason = "SUPPRESSED", "USER_ALERTS_DISABLED"
        elif capability and not self.capabilities.has(telegram_id, capability) and not critical_live:
            status, reason = "SUPPRESSED", "ENTITLEMENT_REQUIRED"
        elif (required_category and required_category not in categories and "ALL" not in categories
              and not critical_live):
            status, reason = "SUPPRESSED", "CATEGORY_DISABLED"
        debounce_cutoff = (occurred - timedelta(minutes=self.debounce_minutes)).isoformat()
        identity = f"{telegram_id}|{canonical}|{timeframe.lower()}|{kind}|{state_identity}"
        alert_key = hashlib.sha256(identity.encode()).hexdigest()
        with connect() as conn:
            existing = conn.execute("SELECT status FROM intelligence_alert_events WHERE alert_key=?", (alert_key,)).fetchone()
            duplicate = conn.execute("""SELECT id FROM intelligence_alert_events WHERE telegram_id=?
                AND symbol=? AND timeframe=? AND alert_type=? AND occurred_at>=?
                AND status IN ('ELIGIBLE','DELIVERED') LIMIT 1""",
                (telegram_id, canonical, timeframe.lower(), kind, debounce_cutoff)).fetchone()
            if existing and status == "ELIGIBLE":
                status, reason = "SUPPRESSED", "UNCHANGED_STATE"
            elif duplicate and status == "ELIGIBLE" and kind not in IDENTITY_SENSITIVE_TYPES:
                status, reason = "SUPPRESSED", "DEBOUNCE_WINDOW"
        if status == "ELIGIBLE" and not critical_live:
            allowance = self.usage.consume(telegram_id, "INTELLIGENCE_ALERT", "alert_daily",
                                           metadata={"type": kind, "symbol": canonical})
            if not allowance["allowed"]:
                status, reason = "SUPPRESSED", "DAILY_LIMIT"
        with connect() as conn:
            conn.execute("""INSERT INTO intelligence_alert_events(alert_key,telegram_id,symbol,timeframe,
                alert_type,severity,entitlement_capability,details_json,status,occurred_at,delivered_at,
                suppressed_reason,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(alert_key) DO NOTHING""",
                (alert_key, telegram_id, canonical, timeframe.lower(), kind, severity.upper(), capability,
                 json.dumps(details or {}, sort_keys=True), status, occurred.isoformat(), None, reason,
                 datetime.now(timezone.utc).isoformat()))
        return {"alert_key": alert_key, "status": status, "suppressed_reason": reason,
                "capability": capability, "category": required_category or "LEGACY",
                "critical_live_override": critical_live,
                "version": "alert-engine-v3", "economic_authority": False}

    @staticmethod
    def mark_delivered(alert_key: str, delivered_at: datetime | None = None) -> bool:
        when = (delivered_at or datetime.now(timezone.utc)).isoformat()
        with connect() as conn:
            result = conn.execute("""UPDATE intelligence_alert_events SET status='DELIVERED',delivered_at=?,
                suppressed_reason=NULL WHERE alert_key=? AND status='ELIGIBLE'""", (when, alert_key))
        return result.rowcount > 0

    @staticmethod
    def mark_delivery_failed(alert_key: str) -> bool:
        with connect() as conn:
            result = conn.execute("""UPDATE intelligence_alert_events SET status='DELIVERY_FAILED',
                suppressed_reason='TELEGRAM_DELIVERY_FAILED' WHERE alert_key=? AND status='ELIGIBLE'""",
                (alert_key,))
        return result.rowcount > 0
