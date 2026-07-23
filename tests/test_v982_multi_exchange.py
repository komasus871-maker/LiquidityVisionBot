from decimal import Decimal

import pytest

from services.exchanges.base import ExchangeRequestError
from services.exchanges.binance_usdm import BinanceUsdmAdapter
from services.exchanges.bybit_v5 import BybitV5Adapter
from services.exchanges.models import ExchangeCredentials, ExchangeName, ExchangeStatus
from services.exchanges.registry import build_exchange_registry
from version import APP_VERSION, RELEASE_NAME


class StubBybit(BybitV5Adapter):
    def __init__(self, responses, configured=True):
        super().__init__(ExchangeCredentials("key" if configured else "", "secret" if configured else "", True))
        self.responses = responses
        self.calls = []

    async def _request(self, path, *, params=None, signed=False):
        self.calls.append((path, params, signed))
        result = self.responses[path]
        if isinstance(result, Exception):
            raise result
        return result


def test_release_and_registry(monkeypatch):
    monkeypatch.setenv("BYBIT_TESTNET", "true")
    registry = build_exchange_registry()
    assert APP_VERSION == "9.8.8"
    assert "Autonomous Demo Execution Core" in RELEASE_NAME
    assert registry.available() == (ExchangeName.BINANCE, ExchangeName.BINGX, ExchangeName.BYBIT, ExchangeName.OKX)
    assert isinstance(registry.create(ExchangeName.BYBIT), BybitV5Adapter)


@pytest.mark.asyncio
async def test_bybit_health_public_only():
    adapter = StubBybit({"/v5/market/time": {"timeSecond": "123"}}, configured=False)
    async def fake_time(): return 123000, 3.2
    adapter._server_time = fake_time
    health = await adapter.health()
    assert health.status is ExchangeStatus.PUBLIC_ONLY
    assert health.endpoint == BybitV5Adapter.TESTNET_URL


@pytest.mark.asyncio
async def test_bybit_balance_position_order_and_rules_mapping():
    adapter = StubBybit({
        "/v5/account/wallet-balance": {"list": [{"coin": [{"coin": "USDT", "walletBalance": "100", "equity": "101", "availableToWithdraw": "80", "unrealisedPnl": "1"}]}]},
        "/v5/position/list": {"list": [{"symbol": "BTCUSDT", "side": "Buy", "size": "0.01", "avgPrice": "60000", "markPrice": "61000", "unrealisedPnl": "10", "leverage": "5", "liqPrice": "50000"}]},
        "/v5/order/realtime": {"list": [{"orderId": "7", "symbol": "BTCUSDT", "side": "Sell", "orderType": "Limit", "orderStatus": "New", "qty": "0.01", "cumExecQty": "0", "price": "65000", "triggerPrice": "0", "reduceOnly": True}]},
        "/v5/market/instruments-info": {"list": [{"symbol": "BTCUSDT", "status": "Trading", "baseCoin": "BTC", "quoteCoin": "USDT", "priceFilter": {"tickSize": "0.10"}, "lotSizeFilter": {"qtyStep": "0.001", "minOrderQty": "0.001", "minNotionalValue": "5"}}]},
    })
    balances = await adapter.balances(); positions = await adapter.positions(); orders = await adapter.open_orders("btcusdt"); rules = await adapter.symbol_rules("btcusdt")
    assert balances[0].wallet_balance == Decimal("100")
    assert positions[0].side == "BUY" and positions[0].quantity == Decimal("0.01")
    assert orders[0].reduce_only is True
    assert rules.price_tick == Decimal("0.10") and rules.min_notional == Decimal("5")


@pytest.mark.asyncio
async def test_binance_451_is_geo_blocked():
    adapter = BinanceUsdmAdapter(ExchangeCredentials("", "", True))
    async def blocked(): raise ExchangeRequestError("Binance API error 451 (0): restricted location")
    adapter._server_time = blocked
    health = await adapter.health()
    assert health.status is ExchangeStatus.GEO_BLOCKED
    assert health.reachable is False
