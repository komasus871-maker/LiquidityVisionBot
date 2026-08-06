from __future__ import annotations

import time
from decimal import Decimal

import pytest

from database.database import connect, create_tables
from services.bingx_sync import BingXAccountSyncService
from services.exchanges.base import (
    ExchangeAuthenticationError, ExchangeTimeoutError, ExchangeTimestampError,
)
from services.exchanges.models import (
    ExchangeAccountInfo, ExchangeBalance, ExchangeCapabilities, ExchangeCapability,
    SymbolRules,
)
from services.live_accounts import LiveAccountRepository


@pytest.fixture()
def sync_db(monkeypatch, tmp_path):
    monkeypatch.setattr("database.database.USE_POSTGRES", False)
    monkeypatch.setattr("database.database.DATABASE_NAME", tmp_path / "sync.db")
    create_tables()
    with connect() as conn:
        conn.execute("""
            INSERT INTO live_exchange_accounts(telegram_id,exchange,credential_ref,created_at,updated_at)
            VALUES(7,'bingx','ref','now','now')
        """)


class SyncAdapter:
    ADAPTER_VERSION = "9.9.11"
    environment = "prod-live"

    def __init__(self, failure=None):
        self.failure = failure or {}
        self.calls = []

    async def _stage(self, name, value):
        self.calls.append(name)
        error = self.failure.get(name)
        if error:
            raise error
        return value

    async def server_time(self):
        return await self._stage("SERVER_TIME", int(time.time() * 1000))

    async def account_info(self):
        return await self._stage("ACCOUNT", ExchangeAccountInfo(
            True, None, "ISOLATED", "HEDGE", self.environment))

    async def balances(self):
        return await self._stage("BALANCES", [ExchangeBalance(
            "USDT", Decimal("100"), Decimal("80"))])

    async def positions(self):
        return await self._stage("POSITIONS", [])

    async def open_orders(self, symbol=None):
        return await self._stage("OPEN_ORDERS", [])

    async def symbol_rules(self, symbol):
        return await self._stage("SYMBOL_RULES", SymbolRules(
            "BTC-USDT", "TRADING", "BTC", "USDT", Decimal("0.1"),
            Decimal("0.001"), Decimal("0.001"), Decimal("5"), Decimal("10"), 20))

    async def margin_mode(self, symbol):
        return await self._stage("MARGIN_MODE", "ISOLATED")

    def capabilities(self):
        return ExchangeCapabilities(frozenset(ExchangeCapability))


@pytest.mark.asyncio
async def test_successful_sync_invokes_every_stage_and_persists(sync_db, caplog):
    adapter = SyncAdapter()
    with caplog.at_level("INFO"):
        report = await BingXAccountSyncService(adapter).synchronize(
            telegram_id=7, account_id=1, symbol="BTCUSDT")
    assert report.success
    assert adapter.calls == [
        "SERVER_TIME", "ACCOUNT", "BALANCES", "POSITIONS",
        "OPEN_ORDERS", "SYMBOL_RULES", "MARGIN_MODE",
    ]
    account = LiveAccountRepository().get(7, "bingx")
    assert account.adapter_version == "9.9.11"
    assert account.adapter_environment == "prod-live"
    assert account.last_sync_at and account.sync_status == "SUCCESS"
    assert account.account_mode == "HEDGE" and account.margin_mode == "ISOLATED"
    assert account.server_time_drift_ms is not None
    assert "SERVER_TIME" in caplog.text and "SYMBOL_RULES" in caplog.text and "COMPLETE" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(("stage", "error", "code"), [
    ("ACCOUNT", ExchangeAuthenticationError("bad credentials"), "AUTHENTICATION_FAILED"),
    ("SERVER_TIME", ExchangeTimestampError("timestamp rejected"), "TIMESTAMP_OUT_OF_SYNC"),
    ("SERVER_TIME", ExchangeTimeoutError("network timeout"), "TIMEOUT"),
    ("SYMBOL_RULES", ExchangeTimeoutError("rules unavailable"), "TIMEOUT"),
])
async def test_sync_failure_is_stage_specific_and_persisted(sync_db, stage, error, code):
    report = await BingXAccountSyncService(SyncAdapter({stage: error})).synchronize(
        telegram_id=7, account_id=1, symbol="BTCUSDT")
    assert not report.success and report.stage == stage and report.error_code == code
    account = LiveAccountRepository().get(7, "bingx")
    assert account.sync_status == "FAILED" and account.sync_stage == stage
    assert account.sync_error_code == code and account.sync_error_message
    assert account.last_sync_at is None
