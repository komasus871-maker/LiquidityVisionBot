from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from database.database import connect
from services.capabilities import CapabilityService
from services.user_preferences import UserPreferenceService
from utils.symbols import normalize_usdt_symbol


ALERT_CAPABILITY = {
    "ORDER_BOOK_WALL_APPEARS": "ADVANCED_ALERTS", "WALL_REMOVED": "ADVANCED_ALERTS",
    "WALL_REPLENISHED": "ADVANCED_ALERTS", "OI_ACCELERATION": "ADVANCED_ALERTS",
    "FUNDING_EXTREME": "ADVANCED_ALERTS", "LIQUIDITY_SWEEP": "ADVANCED_ALERTS",
}


class IntelligenceAlertService:
    """Persist-first, entitlement-aware, debounced alert eligibility engine."""

    def __init__(self, debounce_minutes: int = 30) -> None:
        self.debounce_minutes = max(1, int(debounce_minutes))
        self.capabilities = CapabilityService()
        self.preferences = UserPreferenceService()

    def evaluate(self, telegram_id: int, *, symbol: str, timeframe: str, alert_type: str,
                 state_identity: str, severity: str = "INFO",
                 details: dict[str, Any] | None = None, occurred_at: datetime | None = None) -> dict[str, Any]:
        canonical = normalize_usdt_symbol(symbol)
        kind = str(alert_type).strip().upper()
        occurred = occurred_at or datetime.now(timezone.utc)
        capability = ALERT_CAPABILITY.get(kind)
        categories = self.preferences.get(telegram_id)["notification_categories"]
        status, reason = "ELIGIBLE", None
        if not categories:
            status, reason = "SUPPRESSED", "USER_ALERTS_DISABLED"
        elif capability and not self.capabilities.has(telegram_id, capability):
            status, reason = "SUPPRESSED", "ENTITLEMENT_REQUIRED"
        debounce_cutoff = (occurred - timedelta(minutes=self.debounce_minutes)).isoformat()
        with connect() as conn:
            duplicate = conn.execute("""SELECT id FROM intelligence_alert_events WHERE telegram_id=?
                AND symbol=? AND timeframe=? AND alert_type=? AND occurred_at>=?
                AND status IN ('ELIGIBLE','DELIVERED') LIMIT 1""",
                (telegram_id, canonical, timeframe.lower(), kind, debounce_cutoff)).fetchone()
            if duplicate and status == "ELIGIBLE":
                status, reason = "SUPPRESSED", "DEBOUNCE_WINDOW"
            identity = f"{telegram_id}|{canonical}|{timeframe.lower()}|{kind}|{state_identity}"
            alert_key = hashlib.sha256(identity.encode()).hexdigest()
            conn.execute("""INSERT INTO intelligence_alert_events(alert_key,telegram_id,symbol,timeframe,
                alert_type,severity,entitlement_capability,details_json,status,occurred_at,delivered_at,
                suppressed_reason,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(alert_key) DO NOTHING""",
                (alert_key, telegram_id, canonical, timeframe.lower(), kind, severity.upper(), capability,
                 json.dumps(details or {}, sort_keys=True), status, occurred.isoformat(), None, reason,
                 datetime.now(timezone.utc).isoformat()))
        return {"alert_key": alert_key, "status": status, "suppressed_reason": reason,
                "capability": capability, "economic_authority": False}

