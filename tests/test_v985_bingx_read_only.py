from decimal import Decimal

import pytest

from services.exchanges.bingx_swap import BingXSwapAdapter, _symbol
from services.exchanges.models import ExchangeCredentials, ExchangeName
from services.exchanges.registry import build_exchange_registry
from version import APP_VERSION


def test_version_and_registry(monkeypatch):
    assert APP_VERSION == "9.9.4"
    registry = build_exchange_registry()
    assert ExchangeName.BINGX in registry.available()
    adapter = registry.create(ExchangeName.BINGX)
    assert isinstance(adapter, BingXSwapAdapter)


def test_bingx_symbol_normalization():
    assert _symbol("BTCUSDT") == "BTC-USDT"
    assert _symbol("btc-usdt") == "BTC-USDT"


@pytest.mark.asyncio
async def test_public_health_and_symbol_rules(monkeypatch):
    adapter = BingXSwapAdapter(ExchangeCredentials("", "", True), max_attempts=1)

    async def fake_request(path, **kwargs):
        if path.endswith("server/time"):
            return {"serverTime": 123456789}
        return [{
            "symbol": "BTC-USDT", "status": "TRADING", "asset": "BTC", "currency": "USDT",
            "pricePrecision": 1, "quantityPrecision": 3, "tradeMinQuantity": "0.001",
        }]

    monkeypatch.setattr(adapter, "_request", fake_request)
    health = await adapter.health()
    assert health.exchange is ExchangeName.BINGX
    assert health.reachable and not health.authenticated
    rules = await adapter.symbol_rules("BTCUSDT")
    assert rules.symbol == "BTC-USDT"
    assert rules.price_tick == Decimal("0.1")
    assert rules.quantity_step == Decimal("0.001")
    assert rules.min_quantity == Decimal("0.001")
