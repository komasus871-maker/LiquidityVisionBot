from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken

from database.database import connect
from services.exchanges.base import ExchangeConfigurationError
from services.exchanges.models import ExchangeCredentials, ExchangeName


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

    def __init__(self, master_key: str | None = None) -> None:
        raw = (master_key or os.getenv("EXCHANGE_CREDENTIALS_MASTER_KEY", "")).strip()
        if not raw:
            raise ExchangeConfigurationError(
                "EXCHANGE_CREDENTIALS_MASTER_KEY is missing; generate a Fernet key before connecting accounts"
            )
        try:
            key = raw.encode("ascii")
            Fernet(key)
        except Exception:
            # Accept a long passphrase while deriving a valid, stable Fernet key.
            key = base64.urlsafe_b64encode(hashlib.sha256(raw.encode("utf-8")).digest())
        self._fernet = Fernet(key)

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")
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
            1 if testnet else 0, "connected", now, now,
        )
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO user_exchange_credentials(
                    telegram_id, exchange, api_key_encrypted, api_secret_encrypted,
                    passphrase_encrypted, testnet, status, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(telegram_id, exchange) DO UPDATE SET
                    api_key_encrypted=excluded.api_key_encrypted,
                    api_secret_encrypted=excluded.api_secret_encrypted,
                    passphrase_encrypted=excluded.passphrase_encrypted,
                    testnet=excluded.testnet,
                    status=excluded.status,
                    updated_at=excluded.updated_at
                """,
                values,
            )
            conn.commit()

    def get(self, telegram_id: int, exchange: ExchangeName) -> UserExchangeConnection | None:
        with connect() as conn:
            row = conn.execute(
                "SELECT * FROM user_exchange_credentials WHERE telegram_id=? AND exchange=? AND status='connected'",
                (int(telegram_id), exchange.value),
            ).fetchone()
        if not row:
            return None
        return UserExchangeConnection(
            telegram_id=int(row["telegram_id"]),
            exchange=exchange,
            credentials=ExchangeCredentials(
                api_key=self.cipher.decrypt(str(row["api_key_encrypted"])),
                api_secret=self.cipher.decrypt(str(row["api_secret_encrypted"])),
                testnet=bool(row["testnet"]),
            ),
            passphrase=self.cipher.decrypt(str(row["passphrase_encrypted"])) if row["passphrase_encrypted"] else "",
            status=str(row["status"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

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
            conn.commit()
            return bool(cursor.rowcount)
