from __future__ import annotations

from pathlib import Path

from cryptography.fernet import Fernet

from services.exchanges.credentials_store import CredentialCipher
from services.exchanges.models import ExchangeCredentials, ExchangeName
from services.exchanges.registry import build_exchange_registry
from services.exchanges.bingx_swap import BingXSwapAdapter


def test_credential_cipher_round_trip_and_no_plaintext() -> None:
    cipher = CredentialCipher(Fernet.generate_key().decode())
    secret = "super-secret-value"
    token = cipher.encrypt(secret)
    assert token != secret
    assert secret not in token
    assert cipher.decrypt(token) == secret


def test_registry_accepts_per_user_bingx_credentials() -> None:
    credentials = ExchangeCredentials("user-key", "user-secret", testnet=True)
    registry = build_exchange_registry(credentials_override={ExchangeName.BINGX: credentials})
    adapter = registry.create(ExchangeName.BINGX)
    assert isinstance(adapter, BingXSwapAdapter)
    assert adapter.credentials == credentials


def test_database_schema_is_isolated_by_user_and_exchange() -> None:
    source = Path("database/database.py").read_text()
    assert "CREATE TABLE IF NOT EXISTS user_exchange_credentials" in source
    assert "UNIQUE(telegram_id, exchange)" in source
    assert "api_key_encrypted" in source
    assert "api_secret_encrypted" in source


def test_handlers_do_not_use_global_execution_for_user_orders() -> None:
    source = Path("handlers/exchanges.py").read_text()
    assert "_user_execution_manager(message.from_user.id, exchange)" in source
    assert "_user_adapter_call(message.from_user.id" in source
    assert "Connect exchange accounts only in a private chat" in source
    assert "await message.delete()" in source
