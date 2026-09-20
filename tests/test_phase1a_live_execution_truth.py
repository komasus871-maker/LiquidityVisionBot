from __future__ import annotations

from decimal import Decimal

import pytest

from database.database import connect, create_tables
from services.execution_models import ExecutionMode
from services.exchanges.base import (
    ExchangeAdapter, ExchangeOrderRejectedError, ExchangeRequestError, ExchangeResponseError,
    ExchangeTimeoutError,
)
from services.exchanges.models import (
    ExchangeCapabilities, ExchangeCapability, ExchangeFill, ExchangeHealth, ExchangeName,
    ExchangeOrder, ExchangeOrderRequest, ExchangePosition, ExchangeStatus, SymbolRules,
)
from services.live_accounts import LiveAccountRepository
from services.live_copy import LiveRecoveryService
from services.live_execution import LiveExecutionCoordinator, LiveExecutionRepository, LiveExecutionState
from services.live_reconciliation import LiveReconciliationService


@pytest.fixture()
def phase1a_db(monkeypatch, tmp_path):
    monkeypatch.setattr("database.database.USE_POSTGRES", False)
    monkeypatch.setattr("database.database.DATABASE_NAME", tmp_path / "phase1a.db")
    create_tables()


def _request(client_id: str, quantity: str = "2") -> ExchangeOrderRequest:
    return ExchangeOrderRequest(
        "BTCUSDT", "BUY", "MARKET", Decimal(quantity), client_id,
        price=Decimal("100"), position_side="LONG",
    )


def _order(client_id: str, *, status: str = "NEW", executed: str = "0",
           quantity: str = "2", average: str | None = None) -> ExchangeOrder:
    return ExchangeOrder(
        "ex-1", "BTCUSDT", "BUY", "MARKET", status,
        Decimal(quantity), Decimal(executed), client_order_id=client_id,
        average_price=Decimal(average) if average is not None else None,
    )


def _fill(fill_id: str, client_id: str, quantity: str, price: str,
          fee: str = "0") -> ExchangeFill:
    return ExchangeFill(
        fill_id, "ex-1", client_id, "BTCUSDT", "BUY",
        Decimal(quantity), Decimal(price), Decimal(fee), "USDT",
    )


class TruthAdapter(ExchangeAdapter):
    def __init__(self) -> None:
        self.place_calls = 0
        self.place_result: ExchangeOrder | Exception = _order("unset")
        self.by_id: ExchangeOrder | None = None
        self.by_client: ExchangeOrder | None = None
        self.open: list[ExchangeOrder] = []
        self.fill_history: list[ExchangeFill] = []
        self.position_history: list[ExchangePosition] = []
        self.lookup_calls: list[str] = []

    def capabilities(self):
        return ExchangeCapabilities(frozenset(ExchangeCapability))

    async def health(self):
        return ExchangeHealth(ExchangeName.OKX, True, True, True, status=ExchangeStatus.CONNECTED)

    async def balances(self):
        return []

    async def positions(self):
        self.lookup_calls.append("positions")
        return self.position_history

    async def open_orders(self, symbol=None):
        self.lookup_calls.append("open_orders")
        return self.open

    async def symbol_rules(self, symbol):
        return SymbolRules(symbol, "TRADING", "BTC", "USDT", Decimal("0.1"),
                           Decimal("0.01"), Decimal("0.01"), Decimal("5"))

    async def place_order(self, request):
        self.place_calls += 1
        if isinstance(self.place_result, Exception):
            raise self.place_result
        return ExchangeOrder(
            self.place_result.order_id, request.symbol, request.side, request.order_type,
            self.place_result.status, request.quantity, self.place_result.executed_quantity,
            client_order_id=request.client_order_id, average_price=self.place_result.average_price,
            commission=self.place_result.commission,
        )

    async def query_order(self, *, symbol, order_id):
        self.lookup_calls.append("order_id")
        return self.by_id

    async def query_order_by_client_id(self, *, symbol, client_order_id):
        self.lookup_calls.append("client_id")
        return self.by_client

    async def fills(self, *, symbol, order_id=None):
        self.lookup_calls.append("fills")
        return list(self.fill_history)


async def _submit(adapter: TruthAdapter, key: str, quantity: str = "2"):
    return await LiveExecutionCoordinator(adapter).submit(
        execution_key=key, plan_id=None, telegram_id=1, account_id=1,
        exchange="okx", mode=ExecutionMode.LIVE,
        request=_request(f"client-{key}", quantity), readiness_passed=True,
    )


@pytest.mark.asyncio
async def test_submit_maps_open_rejected_cancelled_and_explicit_rejection(phase1a_db):
    expected = {"NEW": LiveExecutionState.ACKNOWLEDGED,
                "REJECTED": LiveExecutionState.REJECTED,
                "CANCELLED": LiveExecutionState.CANCELLED}
    for status, state in expected.items():
        adapter = TruthAdapter()
        adapter.place_result = _order(f"client-{status}", status=status)
        result = await _submit(adapter, status)
        assert result.state is state
        assert adapter.place_calls == 1

    adapter = TruthAdapter()
    adapter.place_result = ExchangeOrderRejectedError("definitively rejected")
    result = await _submit(adapter, "explicit-rejection")
    assert result.state is LiveExecutionState.REJECTED


@pytest.mark.asyncio
async def test_immediate_fill_uses_exchange_price_quantity_and_fees(phase1a_db):
    adapter = TruthAdapter()
    client = "client-immediate"
    adapter.place_result = _order(client, status="FILLED", executed="2", average="111")
    adapter.fill_history = [
        _fill("f-1", client, ".5", "100", ".10"),
        _fill("f-2", client, "1.5", "120", ".20"),
    ]
    adapter.position_history = [ExchangePosition(
        "BTCUSDT", "LONG", Decimal("2"), Decimal("115"), Decimal("120"),
        Decimal("10"), 1,
    )]
    result = await _submit(adapter, "immediate")
    assert result.state is LiveExecutionState.FILLED
    with connect() as conn:
        execution = conn.execute("SELECT * FROM live_executions WHERE id=?", (result.execution_id,)).fetchone()
        position = conn.execute("SELECT * FROM live_positions WHERE account_id=1").fetchone()
    assert Decimal(str(execution["executed_quantity"])) == Decimal("2")
    assert Decimal(str(execution["average_fill_price"])) == Decimal("115")
    assert Decimal(str(execution["commission"])) == Decimal("0.3")
    assert Decimal(str(position["quantity"])) == Decimal("2")


@pytest.mark.asyncio
async def test_partial_then_full_and_replayed_fills_are_idempotent(phase1a_db):
    adapter = TruthAdapter()
    client = "client-progressive"
    adapter.place_result = _order(client, status="PARTIALLY_FILLED", executed="1", average="100")
    adapter.fill_history = [_fill("f-1", client, "1", "100", ".1")]
    result = await _submit(adapter, "progressive")
    assert result.state is LiveExecutionState.PARTIALLY_FILLED

    adapter.by_id = _order(client, status="FILLED", executed="2", average="105")
    adapter.fill_history.append(_fill("f-2", client, "1", "110", ".2"))
    recovered = await LiveExecutionCoordinator(adapter).recover(result.execution_id)
    replayed = await LiveExecutionCoordinator(adapter).recover(result.execution_id)
    assert recovered.state is replayed.state is LiveExecutionState.FILLED
    with connect() as conn:
        row = conn.execute("SELECT executed_quantity,average_fill_price,commission FROM live_executions").fetchone()
        count = conn.execute("SELECT COUNT(*) n FROM live_execution_fills").fetchone()["n"]
    assert Decimal(str(row["executed_quantity"])) == Decimal("2")
    assert Decimal(str(row["average_fill_price"])) == Decimal("105")
    assert Decimal(str(row["commission"])) == Decimal("0.3")
    assert count == 2


@pytest.mark.asyncio
async def test_order_summary_is_replaced_when_detailed_fills_arrive(phase1a_db):
    adapter = TruthAdapter()
    client = "client-summary"
    adapter.place_result = _order(client, status="FILLED", executed="2", average="105")
    result = await _submit(adapter, "summary")
    assert result.state is LiveExecutionState.FILLED
    adapter.fill_history = [
        _fill("f-summary-1", client, "1", "100", ".1"),
        _fill("f-summary-2", client, "1", "110", ".2"),
    ]
    await LiveExecutionCoordinator(adapter).recover(result.execution_id)
    with connect() as conn:
        rows = conn.execute("SELECT exchange_fill_id FROM live_execution_fills ORDER BY id").fetchall()
        execution = conn.execute("SELECT average_fill_price,commission FROM live_executions").fetchone()
    assert [row["exchange_fill_id"] for row in rows] == ["f-summary-1", "f-summary-2"]
    assert Decimal(str(execution["average_fill_price"])) == Decimal("105")
    assert Decimal(str(execution["commission"])) == Decimal("0.3")


@pytest.mark.asyncio
@pytest.mark.parametrize("truth", ["accepted", "filled", "lost"])
async def test_timeout_never_resubmits_and_recovers_by_exchange_truth(phase1a_db, truth):
    adapter = TruthAdapter()
    adapter.place_result = ExchangeTimeoutError("lost response")
    result = await _submit(adapter, f"timeout-{truth}")
    assert result.state is LiveExecutionState.UNKNOWN
    client = result.client_order_id
    if truth == "accepted":
        adapter.by_client = _order(client, status="NEW")
    elif truth == "filled":
        adapter.fill_history = [_fill("f-timeout", client, "2", "101", ".25")]
        adapter.position_history = [ExchangePosition(
            "BTCUSDT", "LONG", Decimal("2"), Decimal("101"), Decimal("102"),
            Decimal("2"), 1,
        )]
    recovered = await LiveExecutionCoordinator(adapter).recover(result.execution_id)
    assert recovered.state is {"accepted": LiveExecutionState.ACKNOWLEDGED,
                               "filled": LiveExecutionState.FILLED,
                               "lost": LiveExecutionState.RECOVERY_REQUIRED}[truth]
    assert adapter.place_calls == 1


@pytest.mark.asyncio
async def test_transient_lookup_failure_remains_unknown_and_does_not_submit(phase1a_db):
    class UnavailableTruth(TruthAdapter):
        async def query_order_by_client_id(self, **kwargs):
            raise ExchangeRequestError("provider unavailable")
        async def open_orders(self, symbol=None):
            raise ExchangeRequestError("provider unavailable")
        async def fills(self, **kwargs):
            raise ExchangeRequestError("provider unavailable")
        async def positions(self):
            raise ExchangeRequestError("provider unavailable")

    adapter = UnavailableTruth()
    adapter.place_result = ExchangeTimeoutError("lost response")
    result = await _submit(adapter, "provider-down")
    recovered = await LiveExecutionCoordinator(adapter).recover(result.execution_id)
    assert recovered.state is LiveExecutionState.UNKNOWN
    assert adapter.place_calls == 1


@pytest.mark.asyncio
async def test_malformed_post_response_is_ambiguous_and_never_retried(phase1a_db):
    adapter = TruthAdapter()
    adapter.place_result = ExchangeResponseError("accepted response lacked an order identity")
    first = await _submit(adapter, "malformed-response")
    second = await _submit(adapter, "malformed-response")
    assert first.state is second.state is LiveExecutionState.UNKNOWN
    assert adapter.place_calls == 1


@pytest.mark.asyncio
async def test_lookup_sequence_recovers_closed_order_absent_from_open_orders(phase1a_db):
    adapter = TruthAdapter()
    client = "client-closed"
    adapter.place_result = _order(client, status="NEW")
    submitted = await _submit(adapter, "closed")
    adapter.by_id = _order(client, status="FILLED", executed="2", average="103")
    adapter.fill_history = [_fill("f-closed", client, "2", "103", ".2")]
    adapter.position_history = [ExchangePosition(
        "BTCUSDT", "LONG", Decimal("2"), Decimal("103"), Decimal("104"), Decimal("2"), 1,
    )]
    adapter.lookup_calls.clear()
    report = await LiveReconciliationService().reconcile(
        adapter=adapter, telegram_id=1, account_id=1, exchange="okx")
    assert report["status"] == "MATCHED"
    assert report["automatic_repair"] is True
    assert adapter.lookup_calls.index("order_id") < adapter.lookup_calls.index("fills")
    assert LiveExecutionRepository().get(submitted.execution_id)["state"] == "FILLED"


@pytest.mark.asyncio
async def test_fills_only_reconstructs_exchange_position_missing_locally(phase1a_db):
    adapter = TruthAdapter()
    adapter.place_result = ExchangeTimeoutError("accepted but response lost")
    submitted = await _submit(adapter, "fills-only")
    adapter.fill_history = [_fill("f-only", submitted.client_order_id, "2", "99", ".4")]
    adapter.position_history = [ExchangePosition(
        "BTCUSDT", "LONG", Decimal("2"), Decimal("99"), Decimal("100"), Decimal("2"), 1,
    )]
    report = await LiveReconciliationService().reconcile(
        adapter=adapter, telegram_id=1, account_id=1, exchange="okx")
    assert report["status"] == "MATCHED"
    assert LiveExecutionRepository().get(submitted.execution_id)["state"] == "FILLED"
    with connect() as conn:
        position = conn.execute("SELECT quantity,source FROM live_positions WHERE account_id=1").fetchone()
    assert Decimal(str(position["quantity"])) == Decimal("2")
    assert position["source"] == "EXECUTION_LEDGER"


@pytest.mark.asyncio
async def test_startup_recovery_resolves_ambiguous_execution_without_submission(phase1a_db):
    adapter = TruthAdapter()
    adapter.place_result = ExchangeTimeoutError("accepted but response lost")
    submitted = await _submit(adapter, "startup")
    adapter.fill_history = [_fill("f-startup", submitted.client_order_id, "2", "102", ".2")]
    adapter.position_history = [ExchangePosition(
        "BTCUSDT", "LONG", Decimal("2"), Decimal("102"), Decimal("103"), Decimal("2"), 1,
    )]
    report = await LiveRecoveryService().recover(
        adapter=adapter, telegram_id=1, account_id=1, exchange="okx")
    assert report["state"] == "READY"
    assert LiveExecutionRepository().get(submitted.execution_id)["state"] == "FILLED"
    assert adapter.place_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("restart_state", ["SUBMITTING", "SUBMITTED", "UNKNOWN"])
async def test_restart_recovery_handles_inflight_states_without_resubmit(phase1a_db, restart_state):
    adapter = TruthAdapter()
    client = f"client-restart-{restart_state.lower()}"
    execution = LiveExecutionRepository().create(
        execution_key=f"restart-{restart_state}", plan_id=None, telegram_id=1, account_id=1,
        exchange="okx", mode=ExecutionMode.LIVE, request=_request(client))
    with connect() as conn:
        conn.execute("UPDATE live_executions SET state=?,exchange_order_id='ex-1' WHERE id=?",
                     (restart_state, execution["id"]))
    adapter.by_id = _order(client, status="NEW")
    result = await LiveExecutionCoordinator(adapter).recover(execution["id"])
    assert result.state is LiveExecutionState.ACKNOWLEDGED
    assert adapter.place_calls == 0


def test_duplicate_intent_conflict_is_rejected(phase1a_db):
    repository = LiveExecutionRepository()
    repository.create(execution_key="same-intent", plan_id=None, telegram_id=1, account_id=1,
                      exchange="okx", mode=ExecutionMode.LIVE, request=_request("client-same", "1"))
    with pytest.raises(PermissionError, match="IDEMPOTENCY_CONFLICT"):
        repository.create(execution_key="same-intent", plan_id=None, telegram_id=1, account_id=1,
                          exchange="okx", mode=ExecutionMode.LIVE, request=_request("client-same", "2"))


@pytest.mark.asyncio
async def test_duplicate_reconciliation_mismatch_event_is_idempotent(phase1a_db):
    account = LiveAccountRepository().ensure(9, "okx")
    adapter = TruthAdapter()
    adapter.position_history = [ExchangePosition(
        "ETHUSDT", "LONG", Decimal("1"), Decimal("10"), Decimal("11"), Decimal("1"), 1,
    )]
    service = LiveReconciliationService()
    await service.reconcile(adapter=adapter, telegram_id=9, account_id=account.id, exchange="okx")
    await service.reconcile(adapter=adapter, telegram_id=9, account_id=account.id, exchange="okx")
    with connect() as conn:
        count = conn.execute("SELECT COUNT(*) n FROM live_reconciliation_events WHERE account_id=?",
                             (account.id,)).fetchone()["n"]
    assert count == 1
