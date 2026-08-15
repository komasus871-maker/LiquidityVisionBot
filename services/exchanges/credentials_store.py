from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken

from database.database import connect
from services.exchanges.base import ExchangeConfigurationError
from services.exchanges.models import ExchangeCredentials, ExchangeName
from services.live_safety import LiveAuditRepository


@dataclass(frozen=True, slots=True)
class UserExchangeConnection:
    telegram_id: int
    exchange: ExchangeName
    credentials: ExchangeCredentials
    passphrase: str = ""
    status: str = "connected"
    created_at: str = ""
    updated_at: str = ""


class CredentialCipher:
    """Authenticated encryption for exchange credentials stored in the database."""

    def __init__(self, master_key: str | None = None, *, key_version: str | None = None) -> None:
        self.key_version = (key_version or os.getenv("EXCHANGE_CREDENTIALS_KEY_VERSION", "v1")).strip() or "v1"
        configured = {}
        try:
            configured = json.loads(os.getenv("EXCHANGE_CREDENTIALS_MASTER_KEYS_JSON", "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            configured = {}
        raw = (master_key or configured.get(self.key_version)
               or os.getenv("EXCHANGE_CREDENTIALS_MASTER_KEY", "")).strip()
        if not raw:
            raise ExchangeConfigurationError(
                "EXCHANGE_CREDENTIALS_MASTER_KEY is missing; generate a Fernet key before connecting accounts"
            )
        try:
            key = raw.encode("ascii")
            Fernet(key)
        except Exception as exc:
            raise ExchangeConfigurationError(
                "EXCHANGE_CREDENTIALS_MASTER_KEY must be a valid Fernet key"
            ) from exc
        self._fernet = Fernet(key)
        self._keyring: dict[str, Fernet] = {self.key_version: self._fernet}
        for version, candidate in configured.items():
            try:
                encoded = str(candidate).encode("ascii")
                self._keyring[str(version)] = Fernet(encoded)
            except Exception:
                continue

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str, key_version: str | None = None) -> str:
        try:
            cipher = self._keyring.get(key_version or self.key_version)
            if cipher is None:
                raise InvalidToken
            return cipher.decrypt(value.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise ExchangeConfigurationError("stored exchange credentials cannot be decrypted") from exc


class UserExchangeCredentialStore:
    def __init__(self, cipher: CredentialCipher | None = None) -> None:
        self.cipher = cipher or CredentialCipher()

    def save(
        self,
        telegram_id: int,
        exchange: ExchangeName,
        api_key: str,
        api_secret: str,
        *,
        testnet: bool,
        passphrase: str = "",
    ) -> None:
        if not api_key.strip() or not api_secret.strip():
            raise ExchangeConfigurationError("API key and secret are required")
        now = datetime.now(timezone.utc).isoformat()
        values = (
            int(telegram_id), exchange.value, self.cipher.encrypt(api_key.strip()),
            self.cipher.encrypt(api_secret.strip()),
            self.cipher.encrypt(passphrase.strip()) if passphrase.strip() else "",
            1 if testnet else 0, self.cipher.key_version,
            hashlib.sha256(api_key.strip().encode()).hexdigest()[:12], "connected", now, now,
        )
        with connect() as conn:
            existed = bool(conn.execute(
                "SELECT 1 FROM user_exchange_credentials WHERE telegram_id=? AND exchange=?",
                (int(telegram_id), exchange.value)).fetchone())
            conn.execute(
                """
                INSERT INTO user_exchange_credentials(
                    telegram_id, exchange, api_key_encrypted, api_secret_encrypted,
                    passphrase_encrypted, testnet, key_version, key_fingerprint,
                    status, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(telegram_id, exchange) DO UPDATE SET
                    api_key_encrypted=excluded.api_key_encrypted,
                    api_secret_encrypted=excluded.api_secret_encrypted,
                    passphrase_encrypted=excluded.passphrase_encrypted,
                    testnet=excluded.testnet,
                    key_version=excluded.key_version,
                    key_fingerprint=excluded.key_fingerprint,
                    status=excluded.status,
                    updated_at=excluded.updated_at
                """,
                values,
            )
            if existed:
                conn.execute("""UPDATE live_exchange_accounts SET live_enabled=0,kill_switch=1,
                    execution_mode='DISABLED',confirmed_at=NULL,confirmation_hash=NULL,
                    confirmation_expires_at=NULL,lifecycle_state='READ_ONLY_CONNECTED',
                    certification_invalidated_at=?,certification_invalidation_reason='CREDENTIAL_ROTATED',updated_at=?
                    WHERE telegram_id=? AND exchange=?""",
                    (now, now, int(telegram_id), exchange.value))
            else:
                conn.execute("""UPDATE live_exchange_accounts SET lifecycle_state='READ_ONLY_CONNECTED',
                    updated_at=? WHERE telegram_id=? AND exchange=?""",
                    (now, int(telegram_id), exchange.value))
            conn.commit()
        LiveAuditRepository().record(
            event_type="CREDENTIAL_ROTATED" if existed else "CONNECTION_CREATED",
            outcome="COMPLETE", telegram_id=int(telegram_id), exchange=exchange.value,
            metadata={"testnet": bool(testnet), "credential_material_logged": False})

    def get(self, telegram_id: int, exchange: ExchangeName) -> UserExchangeConnection | None:
        with connect() as conn:
            row = conn.execute(
                "SELECT * FROM user_exchange_credentials WHERE telegram_id=? AND exchange=? AND status='connected'",
                (int(telegram_id), exchange.value),
            ).fetchone()
        if not row:
            return None
        key_version = str(row["key_version"] or "v1")
        connection = UserExchangeConnection(
            telegram_id=int(row["telegram_id"]),
            exchange=exchange,
            credentials=ExchangeCredentials(
                api_key=self.cipher.decrypt(str(row["api_key_encrypted"]), key_version),
                api_secret=self.cipher.decrypt(str(row["api_secret_encrypted"]), key_version),
                testnet=bool(row["testnet"]),
            ),
            passphrase=self.cipher.decrypt(str(row["passphrase_encrypted"]), key_version) if row["passphrase_encrypted"] else "",
            status=str(row["status"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )
        LiveAuditRepository().record(
            event_type="CREDENTIAL_ACCESSED", outcome="COMPLETE",
            telegram_id=int(telegram_id), exchange=exchange.value,
            metadata={"key_version": key_version,
                      "key_fingerprint": str(row["key_fingerprint"] or "unknown")})
        return connection

    def list_details(self, telegram_id: int) -> tuple[dict[str, object], ...]:
        with connect() as conn:
            rows = conn.execute("""SELECT exchange,testnet,status,key_version,key_fingerprint
                FROM user_exchange_credentials WHERE telegram_id=? ORDER BY exchange""",
                (int(telegram_id),)).fetchall()
        return tuple({"exchange": str(row["exchange"]), "testnet": bool(row["testnet"]),
                      "status": str(row["status"]), "key_version": str(row["key_version"] or "v1"),
                      "key_fingerprint": str(row["key_fingerprint"] or "unknown")}
                     for row in rows)

    def list(self, telegram_id: int) -> tuple[tuple[ExchangeName, bool, str], ...]:
        with connect() as conn:
            rows = conn.execute(
                "SELECT exchange, testnet, status FROM user_exchange_credentials WHERE telegram_id=? ORDER BY exchange",
                (int(telegram_id),),
            ).fetchall()
        result = []
        for row in rows:
            try:
                name = ExchangeName(str(row["exchange"]))
            except ValueError:
                continue
            result.append((name, bool(row["testnet"]), str(row["status"])))
        return tuple(result)

    def delete(self, telegram_id: int, exchange: ExchangeName) -> bool:
        with connect() as conn:
            cursor = conn.execute(
                "DELETE FROM user_exchange_credentials WHERE telegram_id=? AND exchange=?",
                (int(telegram_id), exchange.value),
            )
            conn.execute("""UPDATE live_exchange_accounts SET live_enabled=0,kill_switch=1,
                execution_mode='DISABLED',lifecycle_state='REVOKED',certification_invalidated_at=?,
                certification_invalidation_reason='CREDENTIAL_REVOKED',updated_at=?
                WHERE telegram_id=? AND exchange=?""",
                (datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat(),
                 int(telegram_id), exchange.value))
            conn.commit()
            removed = bool(cursor.rowcount)
        if removed:
            LiveAuditRepository().record(event_type="CREDENTIAL_REVOKED", outcome="COMPLETE",
                                         telegram_id=int(telegram_id), exchange=exchange.value)
        return removed
