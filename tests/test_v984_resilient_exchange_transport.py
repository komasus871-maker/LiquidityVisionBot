from decimal import Decimal

import pytest

from services.exchanges.base import ExchangeAuthenticationError, ExchangeTimeoutError
from services.exchanges.models import ExchangeCredentials
from services.exchanges.okx_v5 import OkxV5Adapter, _SYMBOL_RULES_CACHE
from services.exchanges.registry import build_exchange_registry
from version import APP_VERSION, RELEASE_NAME


class RetryOkx(OkxV5Adapter):
    def __init__(self, outcomes, **kwargs):
        super().__init__(ExchangeCredentials("", "", True), retry_backoff_seconds=0, **kwargs)
        self.outcomes = list(outcomes)
        self.calls = 0

    async def _request_once(self, path, *, params=None, signed=False):
        self.calls += 1
        value = self.outcomes.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def test_release_and_registry_transport_settings(monkeypatch):
    monkeypatch.setenv("EXCHANGE_CONNECT_TIMEOUT", "2.5")
    monkeypatch.setenv("EXCHANGE_READ_TIMEOUT", "9")
    monkeypatch.setenv("EXCHANGE_MAX_ATTEMPTS", "4")
    adapter = build_exchange_registry().create("okx")
    assert APP_VERSION == "9.9.3"
    assert RELEASE_NAME == "Copy Execution Planning Layer"
    assert adapter.connect_timeout_seconds == 2.5
    assert adapter.read_timeout_seconds == 9
    assert adapter.max_attempts == 4


@pytest.mark.asyncio
async def test_transient_timeout_is_retried_then_succeeds():
    adapter = RetryOkx([ExchangeTimeoutError("OKX request timed out"), [{"ts": "123"}]], max_attempts=3)
    rows = await adapter._request("/api/v5/public/time")
    assert rows == [{"ts": "123"}]
    assert adapter.calls == 2


@pytest.mark.asyncio
async def test_transient_failure_is_bounded():
    adapter = RetryOkx([ExchangeTimeoutError("timeout")] * 3, max_attempts=3)
    with pytest.raises(Exception, match="after 3 attempts"):
        await adapter._request("/api/v5/public/time")
    assert adapter.calls == 3


@pytest.mark.asyncio
async def test_authentication_failure_is_not_retried():
    adapter = RetryOkx([ExchangeAuthenticationError("bad key")], max_attempts=3)
    with pytest.raises(ExchangeAuthenticationError):
        await adapter._request("/api/v5/account/balance", signed=True)
    assert adapter.calls == 1


@pytest.mark.asyncio
async def test_symbol_rules_process_cache_avoids_duplicate_request():
    _SYMBOL_RULES_CACHE.clear()
    payload = [{"instId": "BTC-USDT-SWAP", "state": "live", "ctValCcy": "BTC", "settleCcy": "USDT", "tickSz": "0.1", "lotSz": "0.01", "minSz": "0.01"}]
    adapter = RetryOkx([payload], max_attempts=1, symbol_cache_ttl_seconds=300)
    first = await adapter.symbol_rules("BTCUSDT")
    second = await adapter.symbol_rules("BTC-USDT-SWAP")
    assert first == second
    assert first.price_tick == Decimal("0.1")
    assert adapter.calls == 1
