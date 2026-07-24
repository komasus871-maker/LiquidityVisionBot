from decimal import Decimal

import pytest

from services.exchanges.base import ExchangeAdapter, ExchangeConfigurationError
from services.exchanges.manager import ExchangeManager
from services.exchanges.models import (
    ExchangeBalance,
    ExchangeCredentials,
    ExchangeHealth,
    ExchangeName,
    ExchangeOrder,
    ExchangePosition,
    ExchangeStatus,
    SymbolRules,
)
from services.exchanges.registry import ExchangeRegistry
from services.exchanges.safety import (
    ExecutionSafetyPolicy,
    ExecutionSafetyValidator,
    OrderIntent,
    OrderSide,
)
from version import APP_VERSION, RELEASE_NAME


class FakeAdapter(ExchangeAdapter):
    def __init__(self, authenticated=True):
        self.authenticated = authenticated
        self.closed = False

    async def health(self):
        return ExchangeHealth(
            exchange=ExchangeName.OKX,
            reachable=True,
            authenticated=self.authenticated,
            testnet=True,
            status=ExchangeStatus.CONNECTED if self.authenticated else ExchangeStatus.PUBLIC_ONLY,
            error=None if self.authenticated else "credentials_not_configured",
        )

    async def balances(self):
        return [ExchangeBalance("USDT", Decimal("250"), Decimal("200"))]

    async def positions(self):
        return [ExchangePosition("BTC-USDT-SWAP", "LONG", Decimal("0.01"), Decimal("60000"), Decimal("61000"), Decimal("10"), 3)]

    async def open_orders(self, symbol=None):
        return []

    async def symbol_rules(self, symbol):
        raise NotImplementedError

    async def close(self):
        self.closed = True


def _registry(factory):
    registry = ExchangeRegistry()
    registry.register(ExchangeName.OKX, factory)
    return registry


def _rules():
    return SymbolRules(
        symbol="BTC-USDT-SWAP",
        status="live",
        base_asset="BTC",
        quote_asset="USDT",
        price_tick=Decimal("0.1"),
        quantity_step=Decimal("0.001"),
        min_quantity=Decimal("0.001"),
    )


def test_release_metadata():
    assert APP_VERSION == "9.9.4"
    assert RELEASE_NAME == "Execution Journal & Idempotency Foundation"


@pytest.mark.asyncio
async def test_authenticated_snapshot_is_atomic_and_closes_adapter():
    adapter = FakeAdapter(authenticated=True)
    manager = ExchangeManager(_registry(lambda: adapter))
    snapshot = await manager.snapshot("okx")
    assert snapshot.health.authenticated
    assert snapshot.non_zero_assets == 1
    assert snapshot.open_position_count == 1
    assert snapshot.open_order_count == 0
    assert adapter.closed


@pytest.mark.asyncio
async def test_public_only_snapshot_fails_closed():
    adapter = FakeAdapter(authenticated=False)
    manager = ExchangeManager(_registry(lambda: adapter))
    with pytest.raises(ExchangeConfigurationError):
        await manager.snapshot("okx")
    assert adapter.closed


def test_safe_demo_intent_is_approved():
    policy = ExecutionSafetyPolicy(
        live_enabled=False,
        require_demo=True,
        max_notional_usdt=Decimal("100"),
        max_leverage=5,
        max_open_positions=3,
        allowed_symbols=frozenset({"BTCUSDT"}),
    )
    decision = ExecutionSafetyValidator(policy).validate(
        OrderIntent(ExchangeName.OKX, "BTCUSDT", OrderSide.BUY, Decimal("0.001"), Decimal("60000"), 3, demo=True),
        _rules(),
    )
    assert decision.approved
    assert decision.notional == Decimal("60")
    assert "portfolio_state_not_supplied" in decision.warnings


def test_live_oversized_misaligned_intent_is_rejected():
    policy = ExecutionSafetyPolicy(
        live_enabled=False,
        require_demo=True,
        max_notional_usdt=Decimal("100"),
        max_leverage=5,
        max_open_positions=1,
        allowed_symbols=frozenset({"BTCUSDT"}),
    )
    order = ExchangeOrder("1", "BTC-USDT-SWAP", "BUY", "LIMIT", "NEW", Decimal("0.001"), Decimal("0"))
    decision = ExecutionSafetyValidator(policy).validate(
        OrderIntent(ExchangeName.OKX, "BTCUSDT", OrderSide.BUY, Decimal("0.0015"), Decimal("60000.05"), 10, demo=False),
        _rules(),
        open_orders=(order,),
    )
    assert not decision.approved
    assert "demo_environment_required" in decision.violations
    assert "live_execution_locked" in decision.violations
    assert "duplicate_open_order" in decision.violations
    assert any(item.startswith("leverage_exceeds_limit") for item in decision.violations)
    assert any(item.startswith("quantity_not_aligned") for item in decision.violations)
    assert any(item.startswith("price_not_aligned") for item in decision.violations)
