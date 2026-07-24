from decimal import Decimal

import pytest

from services.exchanges.base import ExchangeAdapter, ExchangeConfigurationError
from services.exchanges.execution import DemoExecutionManager, DemoOrderRequest, ExecutionState
from services.exchanges.models import (
    ExchangeBalance, ExchangeHealth, ExchangeName, ExchangeOrder, ExchangeStatus, SymbolRules,
)
from services.exchanges.registry import ExchangeRegistry
from services.exchanges.safety import OrderSide
from version import APP_VERSION, RELEASE_NAME


class DemoAdapter(ExchangeAdapter):
    def __init__(self, *, demo=True):
        self.demo = demo
        self.create_calls = 0
        self.closed = 0

    async def health(self):
        return ExchangeHealth(ExchangeName.BINGX, True, True, self.demo, status=ExchangeStatus.CONNECTED)

    async def balances(self):
        return [ExchangeBalance("USDT", Decimal("1000"), Decimal("900"))]

    async def positions(self):
        return []

    async def open_orders(self, symbol=None):
        return []

    async def symbol_rules(self, symbol):
        return SymbolRules("BTC-USDT", "TRADING", "BTC", "USDT", Decimal("0.1"), Decimal("0.001"), Decimal("0.001"))

    async def create_demo_order(self, **kwargs):
        self.create_calls += 1
        return ExchangeOrder("42", "BTC-USDT", "BUY", "MARKET", "NEW", Decimal("0.001"), Decimal("0"))

    async def cancel_demo_order(self, *, symbol, order_id):
        return ExchangeOrder(order_id, symbol, "BUY", "LIMIT", "CANCELLED", Decimal("0.001"), Decimal("0"))

    async def demo_order_status(self, *, symbol, order_id):
        return ExchangeOrder(order_id, symbol, "BUY", "LIMIT", "FILLED", Decimal("0.001"), Decimal("0.001"))

    async def close(self):
        self.closed += 1


def registry_for(adapter):
    registry = ExchangeRegistry()
    registry.register(ExchangeName.BINGX, lambda: adapter)
    return registry


def request():
    return DemoOrderRequest(
        ExchangeName.BINGX, "BTCUSDT", OrderSide.BUY, "MARKET",
        Decimal("0.001"), Decimal("60000"), 3,
    )


def test_release_metadata():
    assert APP_VERSION == "9.9.5a"
    assert RELEASE_NAME == "Paper Execution Engine Foundation"


@pytest.mark.asyncio
async def test_demo_execution_is_automatic_and_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("DEMO_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("EXECUTION_ALLOWED_SYMBOLS", "BTCUSDT")
    monkeypatch.setenv("EXECUTION_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    adapter = DemoAdapter(demo=True)
    manager = DemoExecutionManager(registry_for(adapter))
    first = await manager.submit(request())
    second = await manager.submit(request())
    assert first.state is ExecutionState.ACCEPTED
    assert second.client_order_id == first.client_order_id
    assert adapter.create_calls == 1


@pytest.mark.asyncio
async def test_live_credentials_are_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("DEMO_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("EXECUTION_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    manager = DemoExecutionManager(registry_for(DemoAdapter(demo=False)))
    with pytest.raises(ExchangeConfigurationError):
        await manager.submit(request())


@pytest.mark.asyncio
async def test_runtime_kill_switch(monkeypatch):
    monkeypatch.setenv("DEMO_EXECUTION_ENABLED", "true")
    manager = DemoExecutionManager(registry_for(DemoAdapter()))
    manager.kill()
    with pytest.raises(ExchangeConfigurationError):
        await manager.submit(request())
