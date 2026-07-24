from decimal import Decimal

import pytest

from services.exchanges.models import ExchangeCredentials, ExchangeName, ExchangeStatus
from services.exchanges.okx_v5 import OkxV5Adapter, _instrument_id
from services.exchanges.registry import build_exchange_registry
from version import APP_VERSION, RELEASE_NAME


class StubOkx(OkxV5Adapter):
    def __init__(self, responses, configured=True):
        super().__init__(
            ExchangeCredentials("key" if configured else "", "secret" if configured else "", True),
            passphrase="pass" if configured else "",
        )
        self.responses = responses
        self.calls = []

    async def _request(self, path, *, params=None, signed=False):
        self.calls.append((path, params, signed))
        value = self.responses[path]
        if isinstance(value, Exception):
            raise value
        return value


def test_release_registry_and_symbol_normalization(monkeypatch):
    monkeypatch.setenv("OKX_DEMO", "true")
    registry = build_exchange_registry()
    assert APP_VERSION == "9.9.4"
    assert "Execution Journal & Idempotency Foundation" in RELEASE_NAME
    assert ExchangeName.OKX in registry.available()
    assert isinstance(registry.create("okx"), OkxV5Adapter)
    assert _instrument_id("BTCUSDT") == "BTC-USDT-SWAP"
    assert _instrument_id("BTC-USDT-SWAP") == "BTC-USDT-SWAP"


@pytest.mark.asyncio
async def test_okx_public_health_without_credentials():
    adapter = StubOkx({"/api/v5/public/time": [{"ts": "123000"}]}, configured=False)
    async def fake_time(): return 123000, 4.5
    adapter._server_time = fake_time
    health = await adapter.health()
    assert health.status is ExchangeStatus.PUBLIC_ONLY
    assert health.exchange is ExchangeName.OKX
    assert health.endpoint == OkxV5Adapter.BASE_URL


@pytest.mark.asyncio
async def test_okx_read_only_mapping():
    adapter = StubOkx({
        "/api/v5/account/balance": [{"details": [{"ccy": "USDT", "eq": "101", "availEq": "80", "upl": "1"}]}],
        "/api/v5/account/positions": [{"instId": "BTC-USDT-SWAP", "posSide": "long", "pos": "2", "avgPx": "60000", "markPx": "61000", "upl": "20", "lever": "5", "liqPx": "50000"}],
        "/api/v5/trade/orders-pending": [{"ordId": "7", "instId": "BTC-USDT-SWAP", "side": "sell", "ordType": "limit", "state": "live", "sz": "2", "accFillSz": "0", "px": "65000", "reduceOnly": True}],
        "/api/v5/public/instruments": [{"instId": "BTC-USDT-SWAP", "state": "live", "ctValCcy": "BTC", "settleCcy": "USDT", "tickSz": "0.1", "lotSz": "0.01", "minSz": "0.01"}],
    })
    balances = await adapter.balances()
    positions = await adapter.positions()
    orders = await adapter.open_orders("BTCUSDT")
    rules = await adapter.symbol_rules("BTCUSDT")
    assert balances[0].wallet_balance == Decimal("101")
    assert positions[0].side == "LONG" and positions[0].quantity == Decimal("2")
    assert orders[0].reduce_only is True
    assert rules.symbol == "BTC-USDT-SWAP" and rules.price_tick == Decimal("0.1")
    assert not hasattr(adapter, "place_order")
