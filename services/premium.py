import os
from datetime import datetime, timedelta, timezone
from database.database import connect
from services.capabilities import CapabilityService

PREMIUM_STARS = int(os.getenv("PREMIUM_STARS", "199"))
PREMIUM_DAYS = int(os.getenv("PREMIUM_DAYS", "30"))
CRYPTO_PAYMENT_TEXT = os.getenv("CRYPTO_PAYMENT_TEXT", "Crypto payments are temporarily handled manually. Contact project support.")


class PremiumService:
    def __init__(self) -> None:
        self.entitlements = CapabilityService()

    def grant(self, telegram_id: int, days: int = PREMIUM_DAYS, tier: str = "PRO") -> str:
        now = datetime.now(timezone.utc)
        current = self.entitlements.plan(telegram_id)
        remaining = 0
        if current.get("expires_at"):
            try:
                expiry = datetime.fromisoformat(str(current["expires_at"]).replace("Z", "+00:00"))
                remaining = max(0, (expiry - now).days)
            except ValueError:
                remaining = 0
        assigned = self.entitlements.assign_plan(
            telegram_id, tier, source="TELEGRAM_STARS",
            duration_days=max(1, int(days)) + remaining,
            audit_metadata={"payment_product": "PRO_30_DAY"},
        )
        return str(assigned["expires_at"])

    def status(self, telegram_id: int) -> dict:
        plan = self.entitlements.plan(telegram_id)
        with connect() as conn:
            row = conn.execute("SELECT notifications_enabled FROM users WHERE telegram_id=?",
                               (telegram_id,)).fetchone()
        return {"active": plan["plan"] != "FREE", "tier": plan["plan"],
                "until": plan["expires_at"], "notifications": bool(row[0]) if row else True,
                "source": plan["source"], "version": plan["version"]}

    def record_payment(self, telegram_id: int, payment) -> bool:
        charge_id = payment.telegram_payment_charge_id
        with connect() as conn:
            existing = conn.execute("SELECT id FROM payments WHERE telegram_payment_charge_id=?", (charge_id,)).fetchone()
            if existing:
                return False
            conn.execute("""INSERT INTO payments(telegram_id,provider,payload,amount,currency,telegram_payment_charge_id,provider_payment_charge_id,created_at)
                          VALUES(?,?,?,?,?,?,?,?)""",
                         (telegram_id, "TELEGRAM_STARS", payment.invoice_payload, payment.total_amount, payment.currency,
                          charge_id, payment.provider_payment_charge_id, datetime.now(timezone.utc).isoformat()))
        return True

    def payment_history(self, telegram_id: int, limit: int = 5) -> list[dict]:
        with connect() as conn:
            rows = conn.execute("SELECT provider,amount,currency,created_at FROM payments WHERE telegram_id=? ORDER BY id DESC LIMIT ?", (telegram_id, limit)).fetchall()
        return [dict(row) for row in rows]
