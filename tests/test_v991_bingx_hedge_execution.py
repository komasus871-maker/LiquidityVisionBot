from decimal import Decimal

import pytest

from services.exchanges.bingx_swap import BingXSwapAdapter
from services.exchanges.models import ExchangeCredentials, ExchangeName


@pytest.mark.asyncio
async def test_bingx_hedge_open_order_omits_reduce_only():
    adapter = BingXSwapAdapter(ExchangeCredentials("key", "secret", testnet=True))
    calls = []

    async def fake_request(path, *, params=None, signed=False, method="GET"):
        calls.append((path, dict(params or {}), signed, method))
        if path.endswith("/order"):
            return {"order": {"orderId": "1", "symbol": "BTC-USDT", "side": "BUY", "type": "LIMIT", "status": "NEW", "origQty": "0.001", "price": "59000"}}
        return {}

    adapter._request = fake_request
    await adapter.create_demo_order(
        symbol="BTCUSDT", side="BUY", order_type="LIMIT",
        quantity=Decimal("0.001"), price=Decimal("59000"),
        leverage=3, reduce_only=False,
    )

    order_payload = calls[-1][1]
    assert "reduceOnly" not in order_payload
    assert order_payload["positionSide"] == "LONG"


@pytest.mark.asyncio
async def test_bingx_explicit_reduce_only_is_sent_as_true():
    adapter = BingXSwapAdapter(ExchangeCredentials("key", "secret", testnet=True))
    calls = []

    async def fake_request(path, *, params=None, signed=False, method="GET"):
        calls.append((path, dict(params or {}), signed, method))
        if path.endswith("/order"):
            return {"order": {"orderId": "2", "symbol": "BTC-USDT", "side": "SELL", "type": "MARKET", "status": "NEW", "origQty": "0.001"}}
        return {}

    adapter._request = fake_request
    await adapter.create_demo_order(
        symbol="BTCUSDT", side="SELL", order_type="MARKET",
        quantity=Decimal("0.001"), leverage=3,
        reduce_only=True, position_side="LONG",
    )

    assert calls[-1][1]["reduceOnly"] == "true"
