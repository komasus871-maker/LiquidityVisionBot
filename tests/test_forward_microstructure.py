from __future__ import annotations

import asyncio
import ast
import json
import sqlite3
from pathlib import Path

import pytest

from services.forward_event_store import (
    AppendOnlyEventStore, EventType, IntegrityStatus, RawMarketEvent, Venue,
)
from services.forward_event_replay import ForwardEventReplay, iter_raw_events
from services.forward_microstructure_engine import CrossVenueState, L2Book, MicrostructureFeatureEngine, okx_checksum
from services.forward_public_collectors import parse_binance_message, parse_bingx_message, parse_okx_message
from services.forward_shadow_lab import (
    ForwardOutcomeLabeler, ForwardShadowEngine, ShadowExecutionEngine,
    frozen_forward_candidates,
)


def _event(event_type=EventType.TRADE, *, ts=1_700_000_000_000, payload=None, side="BUY",
           price=100.0, quantity=2.0, sequence_start=None, sequence_end=None, previous_sequence=None):
    return RawMarketEvent(
        venue=Venue.BINANCE, market="USD_M_PERPETUAL", symbol="BTCUSDT",
        instrument_type="PERPETUAL", event_type=event_type,
        exchange_ts_ms=ts, receive_ts_ms=ts + 10,
        price=price, quantity=quantity, side=side,
        sequence_start=sequence_start, sequence_end=sequence_end,
        previous_sequence=previous_sequence,
        payload=payload or {"test": True}, connection_id="test-connection",
    )


def _valid_snapshot(venue="BINANCE", symbol="BTCUSDT", timestamp=1_700_000_000_000):
    return {
        "schema_version": "forward-microstructure-feature-v1",
        "timestamp_ms": timestamp, "receive_ts_ms": timestamp + 10,
        "venue": venue, "symbol": symbol,
        "data_quality": {"status": "VALID", "age_ms": 10, "book_status": "VALID"},
        "trade_flow": {"cvd_notional": 1000, "horizons_ms": {
            "5000": {"normalized_delta": .5}, "30000": {"normalized_delta": .5},
        }},
        "book": {"valid": True, "status": "VALID", "best_bid": 99.9, "best_ask": 100.1,
                 "mid": 100.0, "spread_bps": 2.0, "depth_imbalance": .5,
                 "ask_liquidity_within_bps": 10_000, "bid_liquidity_within_bps": 100_000},
        "liquidations": {"60000": {"forced_buy_notional": 0, "forced_sell_notional": 0}},
        "derivatives": {"open_interest": 1000, "open_interest_change": -.01},
        "price_progress_5m_bps": 5.0, "market_state": "BUY_PRESSURE",
        "execution_authority": False,
    }


def test_raw_event_identity_is_stable_and_clock_is_explicit():
    event = _event(payload={"b": 2, "a": 1})
    same = _event(payload={"a": 1, "b": 2})
    assert event.semantic_id == same.semantic_id
    assert event.payload_sha256 == same.payload_sha256
    assert event.receive_ts_ms > event.exchange_ts_ms


def test_append_only_store_retains_duplicates_and_blocks_mutation(tmp_path):
    store = AppendOnlyEventStore(tmp_path / "events.sqlite3")
    first, duplicate = store.append(_event()), store.append(_event())
    assert not first["duplicate"] and duplicate["duplicate"]
    assert duplicate["duplicate_of"] == first["ingest_id"]
    assert store.counts()["raw_events"] == 2
    with sqlite3.connect(store.path) as connection:
        encoding, storage_type = connection.execute(
            "SELECT raw_encoding,typeof(raw_json) FROM raw_events WHERE ingest_id=1"
        ).fetchone()
        assert encoding == "ZLIB_JSON_UTF8" and storage_type == "blob"
        with pytest.raises(sqlite3.IntegrityError, match="APPEND_ONLY"):
            connection.execute("UPDATE raw_events SET symbol='ETHUSDT' WHERE ingest_id=1")


def test_binance_parser_uses_buyer_maker_for_aggressor_and_sequences():
    trade = parse_binance_message({"e": "aggTrade", "E": 1000, "T": 999, "s": "BTCUSDT",
                                    "a": 17, "p": "100", "q": "2", "m": True},
                                   receive_ts_ms=1010, connection_id="c")[0]
    assert trade.side == "SELL" and trade.is_buyer_maker is True
    depth = parse_binance_message({"e": "depthUpdate", "E": 1000, "T": 1000, "s": "BTCUSDT",
                                    "U": 11, "u": 12, "pu": 10, "b": [["99", "1"]], "a": []},
                                   receive_ts_ms=1010, connection_id="c")[0]
    assert (depth.sequence_start, depth.sequence_end, depth.previous_sequence) == (11, 12, 10)


def test_okx_parser_preserves_taker_side_book_checksum_and_liquidation_side():
    trade = parse_okx_message({"arg": {"channel": "trades", "instId": "BTC-USDT-SWAP"},
                               "data": [{"tradeId": "7", "px": "100", "sz": "3", "side": "buy", "ts": "1000"}]},
                              receive_ts_ms=1010, connection_id="c")[0]
    assert trade.side == "BUY" and trade.metadata["aggressor_semantics"] == "SIDE_IS_TAKER_SIDE"
    liquidation = parse_okx_message({"arg": {"channel": "liquidation-orders", "instType": "SWAP"},
                                     "data": [{"instId": "ETH-USDT-SWAP", "details": [
                                         {"side": "sell", "bkPx": "2000", "sz": "4", "ts": "1000"}]}]},
                                    receive_ts_ms=1010, connection_id="c")[0]
    assert liquidation.symbol == "ETHUSDT" and liquidation.side == "SELL"
    assert liquidation.metadata["liquidated_position_side"] == "LONG"


def test_okx_deprecated_zero_checksum_uses_sequence_integrity_only():
    event = parse_okx_message({"arg": {"channel": "books", "instId": "BTC-USDT-SWAP"},
                               "action": "update", "data": [{
                                   "ts": "1000", "seqId": 11, "prevSeqId": 10,
                                   "checksum": 0, "bids": [["99", "2"]], "asks": [],
                               }]}, receive_ts_ms=1010, connection_id="c")[0]
    assert event.payload["checksum"] == 0
    engine = MicrostructureFeatureEngine()
    snapshot = RawMarketEvent(
        venue=Venue.OKX, market="USDT_SWAP", symbol="BTCUSDT", instrument_type="PERPETUAL",
        event_type=EventType.BOOK_SNAPSHOT, exchange_ts_ms=999, receive_ts_ms=1000,
        sequence_start=10, sequence_end=10,
        payload={"bids": [["99", "1"]], "asks": [["101", "1"]], "checksum": 0},
    )
    assert engine.ingest(snapshot)["book_result"]["applied"]


def test_okx_global_liquidation_payload_retains_symbol_for_universe_filtering():
    event = parse_okx_message({"arg": {"channel": "liquidation-orders", "instType": "SWAP"},
                               "data": [{"instId": "XRP-USDT-SWAP", "details": [{
                                   "side": "sell", "bkPx": "1", "sz": "10", "ts": "1000",
                               }]}]}, receive_ts_ms=1010, connection_id="c")[0]
    assert event.symbol == "XRPUSDT"
    assert event.symbol not in ("BTCUSDT", "ETHUSDT", "SOLUSDT")


def test_bingx_parser_is_aggressor_labelled_but_book_is_snapshot_only():
    trade = parse_bingx_message({"dataType": "SOL-USDT@trade", "data": {
        "T": 1000, "s": "SOL-USDT", "p": "150", "q": "2", "m": False,
    }}, receive_ts_ms=1010, connection_id="c")[0]
    assert trade.side == "BUY"
    book = parse_bingx_message({"dataType": "SOL-USDT@depth20@500ms", "data": {
        "T": 1000, "bids": [["149", "2"]], "asks": [["151", "3"]],
    }}, receive_ts_ms=1010, connection_id="c")[0]
    assert book.event_type is EventType.BOOK_SNAPSHOT
    assert book.metadata["sequence_semantics"] == "SNAPSHOT_ONLY_NO_DELTA_CONTINUITY"


def test_bingx_parser_accepts_batched_trade_rows():
    events = parse_bingx_message({"dataType": "BTC-USDT@trade", "data": [
        {"T": 1000, "s": "BTC-USDT", "p": "100", "q": "2", "m": False},
        {"T": 1001, "s": "BTC-USDT", "p": "101", "q": "1", "m": True},
    ]}, receive_ts_ms=1010, connection_id="c")
    assert [event.side for event in events] == ["BUY", "SELL"]


def test_binance_l2_detects_gap_and_requires_resync():
    book = L2Book("BINANCE", "BTCUSDT", sequence_mode="BINANCE")
    assert book.apply_snapshot([["99", "3"]], [["101", "4"]], sequence=10,
                               exchange_ts_ms=1000, receive_ts_ms=1010).applied
    assert book.apply_delta([["100", "1"]], [], sequence_start=11, sequence_end=11,
                            previous_sequence=10, exchange_ts_ms=1020, receive_ts_ms=1030).applied
    gap = book.apply_delta([], [["102", "1"]], sequence_start=13, sequence_end=13,
                           previous_sequence=12, exchange_ts_ms=1040, receive_ts_ms=1050)
    assert gap.status is IntegrityStatus.GAPPED and gap.resync_required and not gap.applied


def test_binance_first_delta_bridges_rest_snapshot_before_pu_continuity():
    book = L2Book("BINANCE", "BTCUSDT", sequence_mode="BINANCE")
    book.apply_snapshot([[99, 3]], [[101, 4]], sequence=100,
                        exchange_ts_ms=1000, receive_ts_ms=1010)
    first = book.apply_delta([[100, 1]], [], sequence_start=99, sequence_end=102,
                             previous_sequence=98, exchange_ts_ms=1020, receive_ts_ms=1030)
    assert first.applied and book.last_sequence == 102
    second = book.apply_delta([], [[102, 1]], sequence_start=103, sequence_end=103,
                              previous_sequence=102, exchange_ts_ms=1040, receive_ts_ms=1050)
    assert second.applied


def test_okx_l2_validates_exact_text_checksum_and_rejects_corruption():
    bids, asks = {99.0: 3.0}, {101.0: 4.0}
    checksum = okx_checksum(bids, asks, {99.0: ("99.0", "3.0")}, {101.0: ("101.0", "4.0")})
    book = L2Book("OKX", "BTCUSDT", sequence_mode="OKX")
    result = book.apply_snapshot([["99.0", "3.0"]], [["101.0", "4.0"]], sequence=10,
                                 exchange_ts_ms=1000, receive_ts_ms=1010, checksum=checksum)
    assert result.applied
    corrupted = book.apply_delta([], [["101.0", "5.0"]], sequence_start=11, sequence_end=11,
                                 previous_sequence=10, exchange_ts_ms=1020, receive_ts_ms=1030,
                                 checksum=123)
    assert corrupted.code == "CHECKSUM_MISMATCH" and corrupted.resync_required


def test_snapshot_only_book_refuses_incremental_updates():
    book = L2Book("BINGX", "BTCUSDT", sequence_mode="SNAPSHOT_ONLY")
    book.apply_snapshot([[99, 1]], [[101, 1]], sequence=1, exchange_ts_ms=1000, receive_ts_ms=1001)
    assert book.apply_delta([], [], sequence_start=2, sequence_end=2, previous_sequence=1,
                            exchange_ts_ms=1002, receive_ts_ms=1003).code == "DELTA_UNSUPPORTED"


def test_feature_engine_uses_only_observed_trades_and_real_cvd():
    engine = MicrostructureFeatureEngine(stale_after_ms=10_000)
    snapshot_event = _event(EventType.BOOK_SNAPSHOT, ts=1_700_000_000_000,
                            payload={"bids": [[99, 5]], "asks": [[101, 5]]},
                            side=None, price=None, quantity=None, sequence_start=10, sequence_end=10)
    engine.ingest(snapshot_event)
    engine.ingest(_event(ts=1_700_000_000_100, side="BUY", price=100, quantity=2, payload={"id": 1}))
    result = engine.ingest(_event(ts=1_700_000_000_200, side="SELL", price=100, quantity=1, payload={"id": 2}))["snapshot"]
    flow = result["trade_flow"]["horizons_ms"]["1000"]
    assert flow["delta_notional"] == 100
    assert flow["normalized_delta"] == pytest.approx(1 / 3)
    assert result["trade_flow"]["cvd_notional"] == 100


def test_cross_venue_state_requires_fresh_independent_venues():
    cross = CrossVenueState(maximum_age_ms=1000)
    first, second = _valid_snapshot("BINANCE"), _valid_snapshot("OKX")
    second["book"] = dict(second["book"], mid=100.1)
    cross.update(first)
    assert cross.features("BTCUSDT", first["receive_ts_ms"])["status"] == "INSUFFICIENT"
    cross.update(second)
    result = cross.features("BTCUSDT", second["receive_ts_ms"])
    assert result["status"] == "VALID" and result["venue_count"] == 2
    assert result["dispersion_bps"] == pytest.approx(10.0)


def test_forward_venue_health_has_explicit_warm_connected_healthy_stale_states(
    tmp_path, monkeypatch,
):
    import services.forward_public_collectors as collectors
    from services.forward_public_collectors import ForwardCollectorSupervisor

    class Connector:
        venue = Venue.BINANCE
        connection_count = 0
        last_error = None
        capabilities = {"trade": True}

    clock = {"now": 1_000_000}
    monkeypatch.setattr(collectors, "now_ms", lambda: clock["now"])
    supervisor = ForwardCollectorSupervisor(
        store=AppendOnlyEventStore(tmp_path / "venue-health.sqlite3"),
        connectors=[Connector()],
    )
    assert supervisor.health()["venues"]["BINANCE"]["state"] == "WARMING"
    supervisor.connectors["BINANCE"].connection_count = 1
    assert supervisor.health()["venues"]["BINANCE"]["state"] == "CONNECTED"
    supervisor.last_event_ms_by_venue["BINANCE"] = clock["now"]
    assert supervisor.health()["venues"]["BINANCE"]["state"] == "HEALTHY"
    clock["now"] += 61_000
    assert supervisor.health()["venues"]["BINANCE"]["state"] == "STALE"
    supervisor.connectors["BINANCE"].last_error = "synthetic"
    assert supervisor.health()["venues"]["BINANCE"]["state"] == "DEGRADED"


def test_shadow_fill_is_conservative_and_has_zero_authority():
    result = ShadowExecutionEngine.simulate_market(
        _valid_snapshot()["book"], direction="LONG", latency_ms=250,
    )
    assert result["simulated_fill_price"] >= result["observable_touch_price"]
    assert result["actual_order_id"] is None and not result["execution_authority"]


def test_forward_candidate_roster_and_registry_are_frozen():
    candidates = frozen_forward_candidates()
    registry = json.loads(Path("research_artifacts/forward_microstructure/experiment-registry.json").read_text())
    assert len(candidates) == 10 and len({x.family for x in candidates}) == 5
    assert {x.candidate_id for x in candidates} == {x["candidate_id"] for x in registry["candidates"]}
    assert not registry["outcomes_inspected"] and not registry["execution_authority"]
    assert registry["evaluation_contract"]["primary_horizon_ms"] == 300_000


def test_shadow_decision_and_future_label_remain_separate(tmp_path):
    store = AppendOnlyEventStore(tmp_path / "shadow.sqlite3")
    labeler = ForwardOutcomeLabeler(store)
    engine = ForwardShadowEngine(store)
    snapshot = _valid_snapshot()
    decisions = engine.evaluate(snapshot, {"status": "INSUFFICIENT", "venue_count": 1})
    assert decisions and all(not item["execution_authority"] for item in decisions)
    labeler.register(decisions[0], 100)
    assert not labeler.observe(venue="BINANCE", symbol="BTCUSDT",
                               observed_ts_ms=decisions[0]["decision_ts_ms"] + 999, mid=101)
    labels = labeler.observe(venue="BINANCE", symbol="BTCUSDT",
                             observed_ts_ms=decisions[0]["decision_ts_ms"] + 1000, mid=101,
                             book=snapshot["book"])
    assert labels[0]["label_only"] and not labels[0]["feature_eligible"]
    assert labels[0]["shadow_execution_outcomes"]["STRESS_LATENCY"]["status"] == "CLOSED"


def test_checkpoint_is_append_only_and_restart_readable(tmp_path):
    path = tmp_path / "restart.sqlite3"
    store = AppendOnlyEventStore(path)
    store.checkpoint(recorded_ts_ms=1000, venue="BINANCE", symbol="BTCUSDT",
                     connection_id="c1", last_sequence=5, state="BOOK_VALID", details={})
    reopened = AppendOnlyEventStore(path)
    assert reopened.latest_checkpoint("BINANCE", "BTCUSDT")["last_sequence"] == 5
    dashboard = reopened.data_quality_dashboard(observed_ts_ms=1001)
    assert dashboard["execution_authority"] is False


def test_restart_marks_unfinished_outcome_horizons_invalid(tmp_path):
    store = AppendOnlyEventStore(tmp_path / "restart-labels.sqlite3")
    engine = ForwardShadowEngine(store)
    decisions = engine.evaluate(_valid_snapshot(), {"status": "INSUFFICIENT", "venue_count": 1})
    assert decisions
    ForwardOutcomeLabeler(store)
    assert store.counts()["outcome_labels"] == len(decisions) * 9
    unresolved = store.unresolved_shadow_decisions((1000, 5000, 15000, 30000, 60000,
                                                    300000, 900000, 1800000, 3600000))
    assert unresolved == []


def test_forward_modules_have_no_live_order_or_private_credential_imports():
    files = [
        Path("services/forward_event_store.py"), Path("services/forward_microstructure_engine.py"),
        Path("services/forward_shadow_lab.py"), Path("services/forward_public_collectors.py"),
    ]
    forbidden_modules = {"services.live_execution", "services.live_copy", "services.copy_execution_planner"}
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        assert not (imports & forbidden_modules)
    source = Path("services/forward_public_collectors.py").read_text(encoding="utf-8")
    assert "API_KEY" not in source and "api_secret" not in source


def test_supervisor_never_admits_duplicate_trade_twice(tmp_path):
    from services.forward_public_collectors import ForwardCollectorSupervisor

    class Connector:
        venue = Venue.BINANCE
        capabilities = {}
        connection_count = 0
        last_error = None
        async def request_resync(self, symbol):
            return None

    store = AppendOnlyEventStore(tmp_path / "supervisor.sqlite3")
    supervisor = ForwardCollectorSupervisor(store=store, connectors=[Connector()])
    event = _event()
    asyncio.run(supervisor.emit(event))
    asyncio.run(supervisor.emit(event))
    assert supervisor.duplicates == 1
    assert supervisor.engine.cvd[("BINANCE", "BTCUSDT")] == 200


def test_receive_order_replay_reproduces_features_and_shadow_decisions(tmp_path):
    from services.forward_public_collectors import ForwardCollectorSupervisor

    class Connector:
        venue = Venue.BINANCE
        capabilities = {}
        connection_count = 0
        last_error = None
        async def request_resync(self, symbol):
            return None

    source = tmp_path / "source.sqlite3"
    store = AppendOnlyEventStore(source)
    supervisor = ForwardCollectorSupervisor(
        store=store, connectors=[Connector()], feature_interval_ms=100,
    )
    snapshot = _event(
        EventType.BOOK_SNAPSHOT, ts=1_700_000_000_000,
        payload={"bids": [["99.99", "100"]], "asks": [["100.01", "1"]]},
        side=None, price=None, quantity=None, sequence_start=10, sequence_end=10,
    )
    trade = _event(ts=1_700_000_000_100, payload={"id": 1})
    asyncio.run(supervisor.emit(snapshot))
    asyncio.run(supervisor.emit(trade))
    assert list(iter_raw_events(source)) == [snapshot, trade]

    result = ForwardEventReplay(source, tmp_path / "replay.sqlite3", feature_interval_ms=100).run()
    assert result.decision_ids_match and result.feature_ids_match
    assert result.events_replayed == 2 and result.counts["shadow_decisions"] > 0
