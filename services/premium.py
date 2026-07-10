import os
from datetime import datetime, timedelta, timezone
from database.database import connect

PREMIUM_STARS = int(os.getenv("PREMIUM_STARS", "199"))
PREMIUM_DAYS = int(os.getenv("PREMIUM_DAYS", "30"))
CRYPTO_PAYMENT_TEXT = os.getenv("CRYPTO_PAYMENT_TEXT", "Crypto payments are temporarily handled manually. Contact project support.")


class PremiumService:
    def grant(self, telegram_id: int, days: int = PREMIUM_DAYS, tier: str = "PREMIUM") -> str:
        now = datetime.now(timezone.utc)
        with connect() as conn:
            row = conn.execute("SELECT premium_until FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()
            base = now
            if row and row[0]:
                try:
                    existing = datetime.fromisoformat(row[0])
                    if existing > now:
                        base = existing
                except ValueError:
                    pass
            until = base + timedelta(days=days)
            conn.execute("UPDATE users SET premium=1,premium_tier=?,premium_until=? WHERE telegram_id=?", (tier, until.isoformat(), telegram_id))
        return until.isoformat()

    def status(self, telegram_id: int) -> dict:
        now = datetime.now(timezone.utc)
        with connect() as conn:
            row = conn.execute("SELECT premium,premium_tier,premium_until,notifications_enabled FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()
        if not row:
            return {"active": False, "tier": "FREE", "until": None, "notifications": True}
        active = bool(row[0])
        until = row[2]
        if until:
            try:
                active = active and datetime.fromisoformat(until) > now
            except ValueError:
                active = False
        return {"active": active, "tier": row[1] or "FREE", "until": until, "notifications": bool(row[3])}

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
