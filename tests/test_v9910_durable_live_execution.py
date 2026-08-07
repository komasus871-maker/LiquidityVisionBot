from __future__ import annotations

from decimal import Decimal

import pytest

from database.database import connect, create_tables
from services.execution_models import ExecutionMode
from services.exchanges.base import (
    ExchangeAdapter, ExchangeRateLimitError, ExchangeTimeoutError,
    ExchangeUnsupportedCapabilityError,
)
from services.exchanges.models import (
    ExchangeCapabilities, ExchangeCapability, ExchangeFill, ExchangeHealth, ExchangeName,
    ExchangeOrder, ExchangeOrderRequest, ExchangePosition, ExchangeStatus, SymbolRules,
)
from services.live_accounts import LiveAccountRepository
from services.live_execution import (
    LiveExecutionCoordinator, LiveExecutionState, normalize_quantity, safe_close_quantity,
    stable_client_order_id, validate_notional,
)
from services.live_readiness import ReadinessContext, configured_mode, evaluate_live_readiness
from version import APP_VERSION, RELEASE_NAME


@pytest.fixture()
def live_db(monkeypatch, tmp_path):
    monkeypatch.setattr("database.database.USE_POSTGRES", False)
    monkeypatch.setattr("database.database.DATABASE_NAME", tmp_path / "v9910.db")
    create_tables()
    create_tables()


class FakeLiveAdapter(ExchangeAdapter):
    def __init__(self, *, timeout=False, missing=False):
        self.timeout = timeout
        self.missing = missing
        self.place_calls = 0
        self.order = ExchangeOrder("ex-1", "BTCUSDT", "BUY", "MARKET", "NEW",
                                   Decimal("2"), Decimal("0"), client_order_id="lv-client")
        self.exchange_fills: list[ExchangeFill] = []

    def capabilities(self):
        return ExchangeCapabilities(frozenset(ExchangeCapability))

    async def health(self):
        return ExchangeHealth(ExchangeName.OKX, True, True, True, status=ExchangeStatus.CONNECTED)

    async def balances(self): return []
    async def positions(self): return []
    async def open_orders(self, symbol=None): return []
    async def symbol_rules(self, symbol):
        return SymbolRules(symbol, "TRADING", "BTC", "USDT", Decimal("0.1"),
                           Decimal("0.01"), Decimal("0.01"), Decimal("5"))

    async def place_order(self, request):
        self.place_calls += 1
        if self.timeout:
            self.timeout = False
            raise ExchangeTimeoutError("timeout api_key=do-not-leak")
        return ExchangeOrder("ex-1", request.symbol, request.side, request.order_type, "NEW",
                             request.quantity, Decimal("0"), client_order_id=request.client_order_id)

    async def query_order_by_client_id(self, *, symbol, client_order_id):
        if self.missing:
            return None
        return ExchangeOrder("ex-1", symbol, "BUY", "MARKET", "NEW", Decimal("2"),
                             Decimal("0"), client_order_id=client_order_id)

    async def fills(self, *, symbol, order_id=None):
        return self.exchange_fills

    async def cancel_order(self, *, symbol, order_id):
        return ExchangeOrder(order_id, symbol, "BUY", "MARKET", "CANCELLED", Decimal("2"), Decimal("0"))


def request(client_id="lv-client", *, reduce_only=False):
    return ExchangeOrderRequest("BTCUSDT", "BUY", "MARKET", Decimal("2"), client_id,
                                price=Decimal("10"), reduce_only=reduce_only)


def test_release_and_modes_default_fail_closed(monkeypatch):
    assert APP_VERSION == "9.9.17"
    assert RELEASE_NAME == "Edge Discovery & Trading Intelligence Engine"
    monkeypatch.delenv("EXECUTION_MODE", raising=False)
    assert configured_mode() is ExecutionMode.PAPER
    monkeypatch.setenv("EXECUTION_MODE", "LIVE")
    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "false")
    assert configured_mode() is ExecutionMode.DISABLED


def test_adapter_capabilities_and_unsupported_operation():
    adapter = FakeLiveAdapter()
    assert adapter.capabilities().supports(ExchangeCapability.PLACE_ORDER)
    basic = type("Basic", (FakeLiveAdapter,), {
        "capabilities": ExchangeAdapter.capabilities,
        "cancel_order": ExchangeAdapter.cancel_order,
    })()
    with pytest.raises(ExchangeUnsupportedCapabilityError):
        import asyncio
        asyncio.run(basic.cancel_order(symbol="BTCUSDT", order_id="1"))


def test_precision_min_notional_stable_identity_and_reduce_only():
    rules = SymbolRules("BTCUSDT", "TRADING", "BTC", "USDT", Decimal(".1"),
                        Decimal(".01"), Decimal(".05"), Decimal("5"))
    assert normalize_quantity(Decimal("1.239"), rules) == Decimal("1.23")
    with pytest.raises(ValueError, match="notional"):
        validate_notional(Decimal(".05"), Decimal("50"), rules)
    assert stable_client_order_id("same") == stable_client_order_id("same")
    assert stable_client_order_id("same") != stable_client_order_id("other")
    assert safe_close_quantity(requested=Decimal("3"), open_quantity=Decimal("2"),
                               side="SELL", position_side="LONG") == Decimal("2")
    with pytest.raises(ValueError, match="increase"):
        safe_close_quantity(requested=Decimal("1"), open_quantity=Decimal("2"),
                            side="BUY", position_side="LONG")


@pytest.mark.asyncio
async def test_reduce_only_request_uses_exchange_truth_and_caps_quantity():
    class Positioned(FakeLiveAdapter):
        async def positions(self):
            return [ExchangePosition("BTCUSDT", "LONG", Decimal("1.25"), Decimal("10"),
                                     Decimal("10"), Decimal("0"), 2)]
    normalized = await LiveExecutionCoordinator(Positioned()).safe_close_request(
        ExchangeOrderRequest("BTCUSDT", "SELL", "MARKET", Decimal("2"), "close", reduce_only=True))
    assert normalized.quantity == Decimal("1.25") and normalized.reduce_only


@pytest.mark.asyncio
async def test_shadow_and_dry_run_never_place_orders(live_db):
    adapter = FakeLiveAdapter()
    coordinator = LiveExecutionCoordinator(adapter)
    for mode, key in ((ExecutionMode.SHADOW, "shadow"), (ExecutionMode.LIVE_DRY_RUN, "dry")):
        result = await coordinator.submit(execution_key=key, plan_id=None, telegram_id=1,
                                          account_id=1, exchange="okx", mode=mode,
                                          request=request(stable_client_order_id(key)))
        assert result.state is LiveExecutionState.CREATED
    assert adapter.place_calls == 0


@pytest.mark.asyncio
async def test_duplicate_submission_is_prevented(live_db):
    adapter = FakeLiveAdapter()
    coordinator = LiveExecutionCoordinator(adapter)
    kwargs = dict(execution_key="unique", plan_id="p", telegram_id=1, account_id=1,
                  exchange="okx", mode=ExecutionMode.LIVE,
                  request=request(stable_client_order_id("unique")), readiness_passed=True)
    first = await coordinator.submit(**kwargs)
    second = await coordinator.submit(**kwargs)
    assert first.state is second.state is LiveExecutionState.ACKNOWLEDGED
    assert adapter.place_calls == 1


@pytest.mark.asyncio
async def test_timeout_after_acceptance_recovers_by_client_id_and_aggregates_fills(live_db):
    adapter = FakeLiveAdapter(timeout=True)
    coordinator = LiveExecutionCoordinator(adapter)
    result = await coordinator.submit(execution_key="ambiguous", plan_id=None, telegram_id=1,
                                      account_id=1, exchange="okx", mode=ExecutionMode.LIVE,
                                      request=request(stable_client_order_id("ambiguous")), readiness_passed=True)
    assert result.state is LiveExecutionState.UNKNOWN
    assert adapter.place_calls == 1
    adapter.exchange_fills = [
        ExchangeFill("f1", "ex-1", result.client_order_id, "BTCUSDT", "BUY", Decimal(".5"), Decimal("10"), Decimal(".1")),
        ExchangeFill("f2", "ex-1", result.client_order_id, "BTCUSDT", "BUY", Decimal("1.5"), Decimal("12"), Decimal(".2")),
    ]
    recovered = await coordinator.recover(result.execution_id)
    assert recovered.state is LiveExecutionState.FILLED
    await coordinator.recover(result.execution_id)  # terminal and idempotent
    with connect() as conn:
        row = conn.execute("SELECT executed_quantity,average_fill_price,commission FROM live_executions WHERE id=?",
                           (result.execution_id,)).fetchone()
        count = conn.execute("SELECT COUNT(*) AS n FROM live_execution_fills WHERE execution_id=?",
                             (result.execution_id,)).fetchone()["n"]
    assert Decimal(str(row["executed_quantity"])) == Decimal("2")
    assert Decimal(str(row["average_fill_price"])) == Decimal("11.5")
    assert Decimal(str(row["commission"])) == Decimal("0.3")
    assert count == 2


@pytest.mark.asyncio
async def test_unknown_stays_recovery_required_when_truth_unavailable(live_db):
    adapter = FakeLiveAdapter(timeout=True, missing=True)
    coordinator = LiveExecutionCoordinator(adapter)
    result = await coordinator.submit(execution_key="missing", plan_id=None, telegram_id=1,
                                      account_id=1, exchange="okx", mode=ExecutionMode.LIVE,
                                      request=request(stable_client_order_id("missing")), readiness_passed=True)
    recovered = await coordinator.recover(result.execution_id)
    assert recovered.state is LiveExecutionState.RECOVERY_REQUIRED
    assert adapter.place_calls == 1


@pytest.mark.asyncio
async def test_retryable_pre_submission_error_uses_retry_wait(live_db):
    class RateLimited(FakeLiveAdapter):
        async def place_order(self, request):
            self.place_calls += 1
            raise ExchangeRateLimitError("slow down token=sensitive")
    adapter = RateLimited()
    result = await LiveExecutionCoordinator(adapter).submit(
        execution_key="retry", plan_id=None, telegram_id=1, account_id=1, exchange="okx",
        mode=ExecutionMode.LIVE, request=request(stable_client_order_id("retry")), readiness_passed=True)
    assert result.state is LiveExecutionState.RETRY_WAIT
    with connect() as conn:
        row = conn.execute("SELECT normalized_error,retry_at FROM live_execution_attempts").fetchone()
    assert "sensitive" not in row["normalized_error"]
    assert row["retry_at"]


@pytest.mark.asyncio
async def test_retry_exhaustion_is_durable(live_db):
    class AlwaysLimited(FakeLiveAdapter):
        async def place_order(self, request):
            self.place_calls += 1
            raise ExchangeRateLimitError("rate limited")
    adapter = AlwaysLimited()
    coordinator = LiveExecutionCoordinator(adapter, max_attempts=2)
    kwargs = dict(execution_key="exhaust", plan_id=None, telegram_id=1, account_id=1,
                  exchange="okx", mode=ExecutionMode.LIVE,
                  request=request(stable_client_order_id("exhaust")), readiness_passed=True)
    first = await coordinator.submit(**kwargs)
    assert first.state is LiveExecutionState.RETRY_WAIT
    with connect() as conn:
        conn.execute("UPDATE live_executions SET next_retry_at='2000-01-01T00:00:00+00:00' WHERE id=?",
                     (first.execution_id,))
    second = await coordinator.submit(**kwargs)
    assert second.state is LiveExecutionState.FAILED
    with connect() as conn:
        attempts = conn.execute("SELECT COUNT(*) AS n FROM live_execution_attempts WHERE execution_id=?",
                                (first.execution_id,)).fetchone()["n"]
    assert attempts == 2 and adapter.place_calls == 2


def test_two_step_confirmation_account_isolation_and_emergency_disable(live_db):
    repo = LiveAccountRepository()
    token = repo.begin_confirmation(1, "okx")
    assert repo.get(1, "okx").live_enabled is False
    assert repo.confirm(2, "okx", token) is False
    assert repo.confirm(1, "okx", token) is True
    confirmed = repo.get(1, "okx")
    assert confirmed.confirmed_at and not confirmed.live_enabled and confirmed.kill_switch
    disabled = repo.emergency_disable(1, "okx")
    assert disabled.execution_mode is ExecutionMode.DISABLED and disabled.kill_switch


def test_readiness_reports_every_failure_and_can_pass():
    failed = evaluate_live_readiness(ReadinessContext(environment="local"))
    assert not failed.ready
    assert "FEATURE_FLAG_DISABLED" in failed.reason_codes
    assert "CAPABILITY_MISSING_PLACE_ORDER" in failed.reason_codes
    ready = evaluate_live_readiness(ReadinessContext(
        environment="production", feature_flag=True, account_enabled=True, confirmed=True,
        credentials_present=True, trading_permission=True, withdrawal_enabled=False,
        account_synced=True, server_time_synced=True, symbol_rules_valid=True,
        portfolio_resolved=True, reconciliation_safe=True, daily_loss_protection=True,
        max_order_notional=100, max_account_exposure=500, max_leverage=3,
        kill_switch_available=True, kill_switch_active=False,
        recent_certification=True, production_adapter_allowed=True, account_mode_known=True,
        capabilities=ExchangeCapabilities(frozenset(ExchangeCapability)),
    ))
    assert ready.ready and not ready.reason_codes
