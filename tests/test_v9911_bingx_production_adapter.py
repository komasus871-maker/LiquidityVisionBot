from __future__ import annotations

import hashlib
import hmac
from decimal import Decimal

import pytest

from database.database import DBConnection, connect, create_tables
from handlers.exchanges import format_bingx_certification
from services.bingx_certification import BingXCertificationService, live_certification_valid
from services.exchanges.base import (
    ExchangeOrderRejectedError, ExchangeRateLimitError, ExchangeTimeoutError, ExchangeTimestampError,
)
from services.exchanges.bingx_swap import BingXSwapAdapter, _SYMBOL_RULES_CACHE, bingx_client_order_id
from services.exchanges.models import (
    ExchangeCapability, ExchangeCredentials, ExchangeOrderRequest,
)
from version import APP_VERSION, RELEASE_NAME


@pytest.fixture()
def bingx_db(monkeypatch, tmp_path):
    monkeypatch.setattr("database.database.USE_POSTGRES", False)
    monkeypatch.setattr("database.database.DATABASE_NAME", tmp_path / "v9911.db")
    create_tables()
    create_tables()
    with connect() as conn:
        conn.execute("""
            INSERT INTO live_exchange_accounts(telegram_id,exchange,credential_ref,created_at,updated_at)
            VALUES(1,'bingx','ref','now','now')
        """)


@pytest.fixture(autouse=True)
def clear_bingx_rules_cache():
    _SYMBOL_RULES_CACHE.clear()


class ContractBingX(BingXSwapAdapter):
    def __init__(self, *, hedge=True, testnet=True):
        super().__init__(ExchangeCredentials("key", "secret", testnet), retry_backoff_seconds=0)
        self.hedge = hedge
        self.calls = []
        self.position_rows = []

    async def _request(self, path, *, params=None, signed=False, method="GET"):
        self.calls.append((path, dict(params or {}), signed, method))
        if path.endswith("server/time"):
            import time
            return {"serverTime": int(time.time() * 1000)}
        if path.endswith("positionSide/dual"):
            return {"dualSidePosition": self.hedge}
        if path.endswith("user/balance"):
            return {"balance": {"asset": "USDT", "balance": "100", "availableMargin": "80"}}
        if path.endswith("user/positions"):
            return self.position_rows
        if path.endswith("trade/openOrders"):
            return {"orders": []}
        if path.endswith("quote/contracts"):
            return [{
                "symbol": "BTC-USDT", "status": "TRADING", "asset": "BTC", "currency": "USDT",
                "pricePrecision": 1, "quantityPrecision": 3, "tradeMinQuantity": "0.001",
                "minNotional": "5", "tradeMaxQuantity": "10", "maxLongLeverage": "20",
            }]
        if path.endswith("marginType"):
            return {"marginType": "ISOLATED"}
        if path.endswith("trade/leverage"):
            return {"longLeverage": "1", "shortLeverage": "1"}
        if path.endswith("trade/order") and method == "POST":
            return {"order": {
                "orderID": "9007199254740993", "clientOrderId": params["clientOrderId"],
                "symbol": params["symbol"], "side": params["side"], "type": params["type"],
                "status": "NEW", "origQty": params["quantity"], "price": params.get("price") or "0",
                "reduceOnly": params.get("reduceOnly", "false"),
            }}
        if path.endswith("trade/order"):
            return {"order": {"orderID": "9007199254740993", "clientOrderId": params.get("clientOrderId"),
                              "symbol": "BTC-USDT", "side": "BUY", "type": "MARKET",
                              "status": "NEW", "origQty": "0.001"}}
        if path.endswith("allFillOrders"):
            return {"fillOrders": [{"tradeId": "f1", "orderID": "9007199254740993",
                                     "clientOrderId": "cid", "symbol": "BTC-USDT", "side": "BUY",
                                     "qty": "0.001", "price": "60000", "commission": "0.02",
                                     "commissionAsset": "USDT", "realizedPnl": "1.2", "time": 123}]}
        return {}


def test_release_environment_capabilities_and_signature():
    assert APP_VERSION == "10.3.0"
    assert RELEASE_NAME == "Operational Intelligence and Fail-Closed LIVE Foundation"
    vst = ContractBingX(testnet=True)
    live = ContractBingX(testnet=False)
    assert vst.environment == "prod-vst" and "open-api-vst" in vst.base_url
    assert live.environment == "prod-live" and "open-api.bingx.com" in live.base_url
    assert vst.capabilities().supports(ExchangeCapability.PLACE_ORDER)
    query, signature = vst.sign_params({"timestamp": 2, "a": 1}, "secret")
    assert query == "a=1&timestamp=2"
    assert signature == hmac.new(b"secret", query.encode(), hashlib.sha256).hexdigest()


def test_client_id_is_deterministic_compliant_and_distinct():
    value = bingx_client_order_id("LV-order/with spaces:" + "x" * 80)
    assert value == bingx_client_order_id("LV-order/with spaces:" + "x" * 80)
    assert value.isalnum() and value.islower() and len(value) <= 40
    assert value != bingx_client_order_id("different")


@pytest.mark.asyncio
async def test_account_balance_symbol_and_fill_normalization():
    adapter = ContractBingX()
    account = await adapter.account_info()
    balances = await adapter.balances()
    rules = await adapter.symbol_rules("BTCUSDT")
    fills = await adapter.fills(symbol="BTCUSDT", order_id="9007199254740993")
    assert account.position_mode == "HEDGE" and account.withdrawal_enabled is None
    assert balances[0].available_balance == Decimal("80")
    assert rules.quantity_step == Decimal("0.001") and rules.min_notional == Decimal("5")
    assert fills[0].fill_id == "f1" and fills[0].commission == Decimal("0.02")
    assert fills[0].realized_pnl == Decimal("1.2")
    fill_call = next(call for call in adapter.calls if call[0].endswith("allFillOrders"))
    assert fill_call[1]["tradingUnit"] == "COIN" and fill_call[1]["startTs"] < fill_call[1]["endTs"]


@pytest.mark.asyncio
async def test_order_rounding_minimums_and_position_modes():
    adapter = ContractBingX(hedge=True)
    normalized = await adapter.normalize_order(ExchangeOrderRequest(
        "BTCUSDT", "BUY", "LIMIT", Decimal("0.0019"), "client", price=Decimal("60000.19"),
        position_side="LONG", leverage=2))
    assert normalized.quantity == Decimal("0.001") and normalized.price == Decimal("60000.1")
    with pytest.raises(ExchangeOrderRejectedError, match="minimum"):
        await adapter.normalize_order(ExchangeOrderRequest(
            "BTCUSDT", "BUY", "LIMIT", Decimal("0.0009"), "small", price=Decimal("60000"),
            position_side="LONG"))
    with pytest.raises(ExchangeOrderRejectedError, match="positionSide"):
        await adapter.normalize_order(ExchangeOrderRequest(
            "BTCUSDT", "BUY", "MARKET", Decimal("0.001"), "ambiguous", price=Decimal("60000")))
    one_way = ContractBingX(hedge=False)
    normalized_one_way = await one_way.normalize_order(ExchangeOrderRequest(
        "BTCUSDT", "BUY", "MARKET", Decimal("0.001"), "oneway", price=Decimal("60000")))
    assert normalized_one_way.position_side == "BOTH"


@pytest.mark.asyncio
async def test_market_limit_and_client_lookup_mapping():
    adapter = ContractBingX()
    market = await adapter.place_order(ExchangeOrderRequest(
        "BTCUSDT", "BUY", "MARKET", Decimal("0.001"), "Market-ID", price=Decimal("60000"),
        position_side="LONG"))
    post = next(call for call in adapter.calls if call[3] == "POST" and call[0].endswith("trade/order"))
    assert market.order_id == "9007199254740993"
    assert post[1]["clientOrderId"] == "marketid" and post[1].get("price") is None
    queried = await adapter.query_order_by_client_id(symbol="BTCUSDT", client_order_id="Market-ID")
    assert queried and queried.order_id == "9007199254740993"


@pytest.mark.asyncio
async def test_hedge_reduce_only_close_uses_position_side_without_reduce_only_field():
    adapter = ContractBingX()
    adapter.position_rows = [{"symbol": "BTC-USDT", "positionAmt": "0.002", "positionSide": "LONG",
                              "avgPrice": "60000", "markPrice": "60100", "leverage": "1"}]
    await adapter.place_order(ExchangeOrderRequest(
        "BTCUSDT", "SELL", "MARKET", Decimal("0.003"), "close", price=Decimal("60000"),
        position_side="LONG", reduce_only=True))
    post = next(call for call in adapter.calls if call[3] == "POST" and call[0].endswith("trade/order"))
    assert post[1]["positionSide"] == "LONG" and post[1]["quantity"] == "0.002"
    assert "reduceOnly" not in post[1]


@pytest.mark.asyncio
async def test_post_is_never_retried_after_timeout():
    class TimeoutAdapter(ContractBingX):
        def __init__(self):
            super().__init__()
            self.once_calls = 0
        async def _request_once(self, path, **kwargs):
            self.once_calls += 1
            raise ExchangeTimeoutError("timeout")
    adapter = TimeoutAdapter()
    with pytest.raises(ExchangeTimeoutError):
        await BingXSwapAdapter._request(adapter, "/economic", signed=True, method="POST")
    assert adapter.once_calls == 1


@pytest.mark.asyncio
async def test_read_rate_limit_is_normalized():
    class Limited(ContractBingX):
        async def _request_once(self, path, **kwargs):
            raise ExchangeRateLimitError("429")
    adapter = Limited()
    adapter.max_attempts = 1
    with pytest.raises(ExchangeRateLimitError):
        await BingXSwapAdapter._request(adapter, "/read")


@pytest.mark.asyncio
async def test_timestamp_error_resynchronizes_only_safe_read():
    class Resync(ContractBingX):
        def __init__(self):
            super().__init__()
            self.outcomes = [ExchangeTimestampError("timestamp"), {"serverTime": 2000}, {"ok": True}]
        async def _request_once(self, path, **kwargs):
            value = self.outcomes.pop(0)
            if isinstance(value, Exception):
                raise value
            return value
    adapter = Resync()
    adapter.max_attempts = 2
    assert await BingXSwapAdapter._request(adapter, "/safe-read") == {"ok": True}
    assert adapter._time_offset_ms != 0


@pytest.mark.asyncio
async def test_live_dry_run_certification_persists_and_never_orders(bingx_db):
    adapter = ContractBingX()
    report = await BingXCertificationService(adapter).dry_run(
        telegram_id=1, account_id=1, symbol="BTCUSDT",
        sample_quantity=Decimal("0.001"), sample_price=Decimal("60000"),
        expected_environment="prod-vst")
    assert report.status == "DRY_RUN_PASSED" and report.order_submission_calls == 0
    assert not any(method in {"POST", "DELETE"} for _, _, _, method in adapter.calls)
    assert "ECONOMIC_VST_CERTIFICATION_REQUIRED" in report.readiness_blockers
    with connect() as conn:
        row = conn.execute("SELECT status,environment FROM bingx_certification_audits").fetchone()
    assert row["status"] == "DRY_RUN_PASSED" and row["environment"] == "prod-vst"
    assert not live_certification_valid(1, environment="prod-vst")
    rendered = format_bingx_certification(report)
    assert "Economic order calls: <b>0</b>" in rendered and "secret" not in rendered


def test_additive_schema_and_postgresql_parameter_translation(bingx_db):
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM bingx_certification_audits").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM exchange_symbol_rules_cache").fetchone()["n"] == 0
    assert DBConnection._translate("SELECT * FROM x WHERE a=? AND b=?") == "SELECT * FROM x WHERE a=%s AND b=%s"


def test_expired_or_dry_certification_never_authorizes_live(bingx_db):
    with connect() as conn:
        conn.execute("""
            INSERT INTO bingx_certification_audits(run_key,telegram_id,account_id,environment,adapter_version,
                certification_type,status,symbol,capability_snapshot_json,permission_snapshot_json,report_json,
                started_at,completed_at,expires_at)
            VALUES('expired',1,1,'prod-vst','9.9.11','VST_ECONOMIC','VST_ECONOMIC_PASSED','BTC-USDT',
                '[]','{}','{}','2000-01-01','2000-01-01','2000-01-02T00:00:00+00:00')
        """)
    assert not live_certification_valid(1, environment="prod-vst")
