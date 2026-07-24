from __future__ import annotations

from decimal import Decimal

import pytest

from services.exchanges.base import ExchangeConfigurationError, ExchangeRequestError
from services.exchanges.binance_usdm import BinanceUsdmAdapter
from services.exchanges.models import ExchangeCredentials, ExchangeName
from services.exchanges.registry import ExchangeRegistry
from version import APP_VERSION, RELEASE_NAME


class StubBinance(BinanceUsdmAdapter):
    def __init__(self, responses: dict[str, object], *, configured: bool = True) -> None:
        credentials = ExchangeCredentials("key" if configured else "", "secret" if configured else "", True)
        super().__init__(credentials)
        self.responses = responses
        self.calls: list[tuple[str, bool]] = []

    async def _request(self, method, path, *, params=None, signed=False):
        self.calls.append((path, signed))
        if signed and not self.credentials.configured:
            raise ExchangeConfigurationError("credentials required")
        response = self.responses[path]
        if isinstance(response, Exception):
            raise response
        return response


@pytest.mark.asyncio
async def test_release_metadata() -> None:
    assert APP_VERSION == "9.9.2"
    assert "Copy Trading Profile Foundation" in RELEASE_NAME


@pytest.mark.asyncio
async def test_binance_balance_mapping_is_read_only() -> None:
    adapter = StubBinance({
        "/fapi/v3/balance": [
            {"asset": "USDT", "balance": "100.5", "availableBalance": "80.25", "crossUnPnl": "1.25"},
            {"asset": "BNB", "balance": "0", "availableBalance": "0", "crossUnPnl": "0"},
        ]
    })
    balances = await adapter.balances()
    assert len(balances) == 1
    assert balances[0].asset == "USDT"
    assert balances[0].wallet_balance == Decimal("100.5")
    assert adapter.calls == [("/fapi/v3/balance", True)]
    assert not hasattr(adapter, "place_order")


@pytest.mark.asyncio
async def test_binance_positions_and_orders_mapping() -> None:
    adapter = StubBinance({
        "/fapi/v3/positionRisk": [
            {"symbol": "BTCUSDT", "positionAmt": "0.01", "entryPrice": "60000", "markPrice": "61000", "unRealizedProfit": "10", "leverage": "5", "liquidationPrice": "50000"},
            {"symbol": "ETHUSDT", "positionAmt": "0"},
        ],
        "/fapi/v1/openOrders": [
            {"orderId": 7, "symbol": "BTCUSDT", "side": "SELL", "type": "LIMIT", "status": "NEW", "origQty": "0.01", "executedQty": "0", "price": "65000", "stopPrice": "0", "reduceOnly": True}
        ],
    })
    positions = await adapter.positions()
    orders = await adapter.open_orders("btcusdt")
    assert positions[0].side == "LONG"
    assert positions[0].quantity == Decimal("0.01")
    assert orders[0].reduce_only is True
    assert orders[0].price == Decimal("65000")


@pytest.mark.asyncio
async def test_symbol_rules_mapping() -> None:
    adapter = StubBinance({
        "/fapi/v1/exchangeInfo": {
            "symbols": [{
                "symbol": "BTCUSDT", "status": "TRADING", "baseAsset": "BTC", "quoteAsset": "USDT",
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                    {"filterType": "MIN_NOTIONAL", "notional": "5"},
                ],
            }]
        }
    })
    rules = await adapter.symbol_rules("btcusdt")
    assert rules.price_tick == Decimal("0.10")
    assert rules.quantity_step == Decimal("0.001")
    assert rules.min_notional == Decimal("5")


@pytest.mark.asyncio
async def test_health_reports_public_only_without_credentials() -> None:
    adapter = StubBinance({"/fapi/v1/time": {"serverTime": 123}}, configured=False)
    async def server_time():
        return 123, 4.2
    adapter._server_time = server_time
    health = await adapter.health()
    assert health.exchange is ExchangeName.BINANCE
    assert health.reachable is True
    assert health.authenticated is False
    assert health.error == "credentials_not_configured"


def test_registry_contract() -> None:
    registry = ExchangeRegistry()
    registry.register(ExchangeName.BINANCE, lambda: StubBinance({}))
    assert registry.available() == (ExchangeName.BINANCE,)
    assert isinstance(registry.create("binance"), BinanceUsdmAdapter)
    with pytest.raises(LookupError):
        registry.create(ExchangeName.BYBIT)
