from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from database.database import connect
from services.execution_models import ExecutionMode


@dataclass(frozen=True, slots=True)
class LiveAccountConfig:
    id: int
    telegram_id: int
    exchange: str
    credential_ref: str
    execution_mode: ExecutionMode
    live_enabled: bool
    dry_run_enabled: bool
    confirmed_at: str | None
    kill_switch: bool
    max_order_notional: float | None
    max_account_exposure: float | None
    max_leverage: int | None


class LiveAccountRepository:
    def credentials_present(self, telegram_id: int, exchange: str) -> bool:
        with connect() as conn:
            row = conn.execute("""
                SELECT 1 AS present FROM user_exchange_credentials
                WHERE telegram_id=? AND exchange=? AND status='connected'
            """, (int(telegram_id), exchange)).fetchone()
        return bool(row)

    def ensure(self, telegram_id: int, exchange: str) -> LiveAccountConfig:
        now = datetime.now(timezone.utc).isoformat()
        credential_ref = f"user_exchange_credentials:{int(telegram_id)}:{exchange}"
        with connect() as conn:
            conn.execute("""
                INSERT INTO live_exchange_accounts(telegram_id,exchange,credential_ref,created_at,updated_at)
                VALUES(?,?,?,?,?) ON CONFLICT(telegram_id,exchange) DO NOTHING
            """, (int(telegram_id), exchange, credential_ref, now, now))
            row = conn.execute("SELECT * FROM live_exchange_accounts WHERE telegram_id=? AND exchange=?",
                               (int(telegram_id), exchange)).fetchone()
        return self._model(row)

    def get(self, telegram_id: int, exchange: str) -> LiveAccountConfig | None:
        with connect() as conn:
            row = conn.execute("SELECT * FROM live_exchange_accounts WHERE telegram_id=? AND exchange=?",
                               (int(telegram_id), exchange)).fetchone()
        return self._model(row) if row else None

    def set_dry_run(self, telegram_id: int, exchange: str, enabled: bool) -> LiveAccountConfig:
        account = self.ensure(telegram_id, exchange)
        mode = ExecutionMode.LIVE_DRY_RUN.value if enabled else ExecutionMode.PAPER.value
        with connect() as conn:
            conn.execute("UPDATE live_exchange_accounts SET dry_run_enabled=?,execution_mode=?,updated_at=? WHERE id=?",
                         (int(enabled), mode, datetime.now(timezone.utc).isoformat(), account.id))
        return self.get(telegram_id, exchange)  # type: ignore[return-value]

    def begin_confirmation(self, telegram_id: int, exchange: str, *, ttl_minutes: int = 10) -> str:
        account = self.ensure(telegram_id, exchange)
        token = f"{secrets.randbelow(1_000_000):06d}"
        digest = hashlib.sha256(token.encode()).hexdigest()
        expires = (datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)).isoformat()
        with connect() as conn:
            conn.execute("""
                UPDATE live_exchange_accounts SET confirmation_hash=?,confirmation_expires_at=?,
                    confirmed_at=NULL,live_enabled=0,kill_switch=1,updated_at=? WHERE id=?
            """, (digest, expires, datetime.now(timezone.utc).isoformat(), account.id))
        return token

    def confirm(self, telegram_id: int, exchange: str, token: str) -> bool:
        now = datetime.now(timezone.utc)
        with connect() as conn:
            row = conn.execute("SELECT * FROM live_exchange_accounts WHERE telegram_id=? AND exchange=?",
                               (int(telegram_id), exchange)).fetchone()
            if not row or not row["confirmation_hash"] or not row["confirmation_expires_at"]:
                return False
            expires = datetime.fromisoformat(str(row["confirmation_expires_at"]).replace("Z", "+00:00"))
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            valid = now <= expires and hmac.compare_digest(
                str(row["confirmation_hash"]), hashlib.sha256(token.strip().encode()).hexdigest())
            if not valid:
                return False
            # Confirmation is necessary but deliberately does not enable LIVE or release the kill switch.
            conn.execute("""
                UPDATE live_exchange_accounts SET confirmed_at=?,confirmation_hash=NULL,
                    confirmation_expires_at=NULL,updated_at=? WHERE id=?
            """, (now.isoformat(), now.isoformat(), row["id"]))
        return True

    def emergency_disable(self, telegram_id: int, exchange: str) -> LiveAccountConfig:
        account = self.ensure(telegram_id, exchange)
        now = datetime.now(timezone.utc).isoformat()
        with connect() as conn:
            conn.execute("""
                UPDATE live_exchange_accounts SET live_enabled=0,dry_run_enabled=0,execution_mode='DISABLED',
                    kill_switch=1,confirmation_hash=NULL,confirmation_expires_at=NULL,updated_at=? WHERE id=?
            """, (now, account.id))
        return self.get(telegram_id, exchange)  # type: ignore[return-value]

    @staticmethod
    def _model(row) -> LiveAccountConfig:
        return LiveAccountConfig(
            id=int(row["id"]), telegram_id=int(row["telegram_id"]), exchange=str(row["exchange"]),
            credential_ref=str(row["credential_ref"]), execution_mode=ExecutionMode(str(row["execution_mode"])),
            live_enabled=bool(row["live_enabled"]), dry_run_enabled=bool(row["dry_run_enabled"]),
            confirmed_at=str(row["confirmed_at"]) if row["confirmed_at"] else None,
            kill_switch=bool(row["kill_switch"]),
            max_order_notional=float(row["max_order_notional"]) if row["max_order_notional"] is not None else None,
            max_account_exposure=float(row["max_account_exposure"]) if row["max_account_exposure"] is not None else None,
            max_leverage=int(row["max_leverage"]) if row["max_leverage"] is not None else None,
        )

    def unresolved(self, telegram_id: int, exchange: str) -> tuple[dict, ...]:
        with connect() as conn:
            rows = conn.execute("""
                SELECT id,client_order_id,symbol,state,recovery_reason,updated_at FROM live_executions
                WHERE telegram_id=? AND exchange=? AND state IN ('UNKNOWN','RECOVERY_REQUIRED','RETRY_WAIT')
                ORDER BY updated_at DESC
            """, (int(telegram_id), exchange)).fetchall()
        return tuple(dict(row) for row in rows)
