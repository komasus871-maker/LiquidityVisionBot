from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest


@pytest.fixture()
def v104_db(tmp_path, monkeypatch):
    monkeypatch.setattr("database.database.USE_POSTGRES", False)
    monkeypatch.setattr("database.database.DATABASE_NAME", tmp_path / "v104.db")
    from database.database import create_tables
    create_tables()
    create_tables()
    return tmp_path / "v104.db"


def _live_account(telegram_id: int = 41):
    from database.database import connect
    from services.live_accounts import LiveAccountRepository

    account = LiveAccountRepository().ensure(telegram_id, "bingx")
    with connect() as conn:
        conn.execute("""UPDATE live_exchange_accounts SET live_enabled=1,
            lifecycle_state='LIVE_ENABLED',execution_mode='LIVE',kill_switch=0 WHERE id=?""",
                     (account.id,))
    return LiveAccountRepository().get_by_id(account.id)


def _settings(account, monkeypatch):
    from services.live_copy import LiveCopySettingsRepository

    monkeypatch.setenv("LIVE_SERVER_MAX_ACCOUNT_EXPOSURE", "500")
    monkeypatch.setenv("LIVE_SERVER_MAX_LEVERAGE", "3")
    return LiveCopySettingsRepository().configure(
        telegram_id=account.telegram_id, account_id=account.id, enabled=True,
        symbols=["BTC-USDT"], strategies=["BREAKOUT"], timeframes=["1h"],
        directions=["BUY"], minimum_quality=Decimal("70"),
        sizing_mode="FIXED_NOTIONAL", sizing_value=Decimal("25"),
        max_exposure=Decimal("100"), max_leverage=2)


def _journal(account):
    plan = {
        "status": "APPROVED", "symbol": "BTCUSDT", "side": "BUY",
        "timeframe": "1h", "strategy": "BREAKOUT", "entry_price": 100,
        "stop_loss": 95, "take_profits": [110], "expected_slippage_pct": 0.01,
    }
    return {"id": 7, "plan_id": "plan-7", "telegram_id": account.telegram_id,
            "signal_id": 8, "exchange_account_id": account.id,
            "status": "EXECUTED", "plan_json": json.dumps(plan)}


def test_queue_is_idempotent_owned_and_seals_exact_request(v104_db, monkeypatch):
    from services.exchanges.models import ExchangeOrderRequest
    from services.live_copy import LiveExecutionQueueRepository

    account = _live_account()
    settings = _settings(account, monkeypatch)
    queue = LiveExecutionQueueRepository()
    first, created = queue.enqueue(journal_row=_journal(account), account_id=account.id,
                                   exchange="bingx", quality=82, settings=settings)
    duplicate, duplicate_created = queue.enqueue(
        journal_row=_journal(account), account_id=account.id,
        exchange="bingx", quality=82, settings=settings)
    assert created and not duplicate_created and first["id"] == duplicate["id"]
    claimed, won = queue.claim(first["id"], worker_id="test")
    assert won and claimed["attempt_count"] == 1
    request = ExchangeOrderRequest(
        symbol="BTCUSDT", side="BUY", order_type="MARKET", quantity=Decimal("0.25"),
        client_order_id="lv-fixed", price=Decimal("100"), leverage=2)
    payload = queue.seal_request(first["id"], request=request,
                                 settings_version=settings["settings_version"])
    assert payload["live_request"]["quantity"] == "0.25"
    assert len(payload["live_request_checksum"]) == 64
    changed = replace(request, quantity=Decimal("0.26"))
    with pytest.raises(PermissionError, match="SEALED_REQUEST_CONFLICT"):
        queue.seal_request(first["id"], request=changed,
                           settings_version=settings["settings_version"])


def test_queue_filters_fail_closed_without_decision_quality(v104_db, monkeypatch):
    from services.live_copy import LiveExecutionQueueRepository

    account = _live_account()
    settings = _settings(account, monkeypatch)
    with pytest.raises(PermissionError, match="LIVE_COPY_MINIMUM_QUALITY"):
        LiveExecutionQueueRepository().enqueue(
            journal_row=_journal(account), account_id=account.id,
            exchange="bingx", quality=None, settings=settings)


def test_sizing_is_deterministic_and_uses_minimum_leverage(v104_db, monkeypatch):
    from services.exchanges.models import ExchangeBalance, SymbolRules
    from services.live_copy import LiveSizer

    monkeypatch.setenv("LIVE_SERVER_MAX_ORDER_NOTIONAL", "80")
    monkeypatch.setenv("LIVE_SERVER_MAX_LEVERAGE", "2")
    result = LiveSizer.calculate(
        settings={"sizing_mode": "EQUITY_PERCENT", "sizing_value": 10,
                  "max_exposure": 100, "max_leverage": 4},
        risk={"max_order_notional": 90, "leverage_cap": 3},
        plan={"entry_price": 100, "stop_loss": 95},
        balances=[ExchangeBalance("USDT", Decimal("1000"), Decimal("500"))],
        rules=SymbolRules("BTCUSDT", "TRADING", "BTC", "USDT", Decimal("0.1"),
                          Decimal("0.01"), Decimal("0.01"), Decimal("5"),
                          max_leverage=10))
    assert result["notional"] == Decimal("80.00")
    assert result["quantity"] == Decimal("0.80")
    assert result["effective_leverage"] == 2


@pytest.mark.asyncio
async def test_daily_pnl_uses_utc_exchange_fills_fees_and_positions(v104_db):
    from services.exchanges.models import (
        ExchangeCapabilities, ExchangeCapability, ExchangeFill, ExchangePosition,
    )
    from services.live_copy import LiveDailyPnlService

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    class Adapter:
        def capabilities(self):
            return ExchangeCapabilities(frozenset({ExchangeCapability.FILLS}))

        async def fills(self, symbol, order_id=None):
            return [ExchangeFill("f1", "o1", "c1", symbol, "BUY", Decimal("1"),
                                 Decimal("100"), Decimal("0.25"), filled_at_ms=now_ms,
                                 realized_pnl=Decimal("-4"))]

        async def positions(self):
            return [ExchangePosition("BTCUSDT", "LONG", Decimal("1"), Decimal("100"),
                                     Decimal("99"), Decimal("-1"), 1)]

    report = await LiveDailyPnlService().refresh(
        adapter=Adapter(), telegram_id=41, account_id=9, exchange="bingx",
        symbols=["BTC-USDT"])
    assert report["bucket_utc"] == datetime.now(timezone.utc).date().isoformat()
    assert report["realized_pnl"] == -4 and report["fees"] == 0.25
    assert report["total_loss_basis"] == -5.25 and report["source_complete"] == 1
    assert LiveDailyPnlService().require_current(9)["state"] == "HEALTHY"


@pytest.mark.asyncio
async def test_daily_pnl_missing_timestamp_is_not_zero(v104_db):
    from services.exchanges.models import ExchangeCapabilities, ExchangeCapability, ExchangeFill
    from services.live_copy import LiveDailyPnlService

    class Adapter:
        def capabilities(self):
            return ExchangeCapabilities(frozenset({ExchangeCapability.FILLS}))

        async def fills(self, symbol, order_id=None):
            return [ExchangeFill("f", "o", None, symbol, "BUY", Decimal("1"), Decimal("1"))]

        async def positions(self):
            return []

    report = await LiveDailyPnlService().refresh(
        adapter=Adapter(), telegram_id=41, account_id=10, exchange="bingx",
        symbols=["BTCUSDT"])
    assert report["state"] == "FAILED" and report["source_complete"] == 0
    assert report["rejection_code"] == "DAILY_PNL_FILL_TIMESTAMP_MISSING"
    with pytest.raises(PermissionError, match="LIVE_DAILY_PNL_UNAVAILABLE"):
        LiveDailyPnlService().require_current(10)


@pytest.mark.asyncio
async def test_emergency_close_is_owned_expiring_and_reduce_only(v104_db):
    from services.exchanges.models import ExchangePosition
    from services.live_copy import LiveEmergencyCloseService
    from services.live_execution import LiveExecutionState, SubmissionResult

    account = _live_account()
    position = ExchangePosition("BTCUSDT", "LONG", Decimal("0.2"), Decimal("100"),
                                Decimal("101"), Decimal("0.2"), 2)

    class Adapter:
        calls = 0

        async def positions(self):
            self.calls += 1
            return [position] if self.calls <= 2 else []

    class Coordinator:
        requests = []

        async def submit(self, **kwargs):
            self.requests.append(kwargs)
            return SubmissionResult(12, LiveExecutionState.ACKNOWLEDGED,
                                    kwargs["request"].client_order_id, "x1")

    adapter, coordinator = Adapter(), Coordinator()
    service = LiveEmergencyCloseService()
    preview = await service.begin(adapter=adapter, telegram_id=account.telegram_id,
                                  account_id=account.id, exchange="bingx")
    with pytest.raises(PermissionError, match="TOKEN_INVALID"):
        await service.confirm(adapter=adapter, telegram_id=999, token=preview["token"],
                              coordinator=coordinator)
    result = await service.confirm(adapter=adapter, telegram_id=account.telegram_id,
                                   token=preview["token"], coordinator=coordinator)
    assert result["state"] == "COMPLETE"
    assert coordinator.requests[0]["request"].reduce_only is True
    assert coordinator.requests[0]["request"].side == "SELL"
    assert coordinator.requests[0]["authority_source"] == "LIVE_EMERGENCY_CLOSE"


@pytest.mark.asyncio
async def test_expired_emergency_confirmation_fails_closed(v104_db):
    from database.database import connect
    from services.exchanges.models import ExchangePosition
    from services.live_copy import LiveEmergencyCloseService

    account = _live_account()

    class Adapter:
        async def positions(self):
            return [ExchangePosition("BTCUSDT", "LONG", Decimal("1"), Decimal("1"),
                                     Decimal("1"), Decimal("0"), 1)]

    service = LiveEmergencyCloseService()
    preview = await service.begin(adapter=Adapter(), telegram_id=account.telegram_id,
                                  account_id=account.id, exchange="bingx")
    with connect() as conn:
        conn.execute("UPDATE live_emergency_confirmations SET expires_at=?",
                     ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),))
    with pytest.raises(PermissionError, match="TOKEN_EXPIRED"):
        await service.confirm(adapter=Adapter(), telegram_id=account.telegram_id,
                              token=preview["token"])


def test_live_tables_and_safe_defaults_are_repeatable(v104_db, monkeypatch):
    from database.database import connect, create_tables
    from services.live_copy import LiveCopyWorker

    create_tables()
    with connect() as conn:
        names = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert {"live_copy_settings", "live_daily_pnl_snapshots", "live_execution_queue",
            "live_emergency_confirmations", "live_recovery_state",
            "market_source_diagnostics"} <= names
    monkeypatch.delenv("LIVE_DISPATCHER_ENABLED", raising=False)
    assert LiveCopyWorker().enabled is False
