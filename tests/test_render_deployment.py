from __future__ import annotations

import json
from pathlib import Path

import database.database as database
from services.forward_event_store import AppendOnlyEventStore, EventType, RawMarketEvent, Venue
from services.forward_partition_store import iter_partition_records
from services.forward_runtime_state import (
    EXPECTED_CANDIDATE_IDS, ForwardRuntimeStateRepository, candidate_identity,
)
from services.market_terminal import (
    render_market_overview, render_order_flow, render_shadow_status,
)


def _snapshot() -> dict:
    return {
        "schema_version": "forward-microstructure-feature-v1",
        "timestamp_ms": 1_789_903_850_000,
        "receive_ts_ms": 1_789_903_850_000,
        "venue": "BINANCE", "symbol": "BTCUSDT",
        "data_quality": {"status": "VALID", "age_ms": 20},
        "trade_flow": {"cvd_notional": 1200, "horizons_ms": {
            "60000": {"normalized_delta": .2}, "300000": {"normalized_delta": .1},
        }},
        "book": {"mid": 100_000, "spread_bps": .5, "depth_imbalance": .2,
                 "microprice": 100_001},
        "liquidations": {"60000": {"forced_buy_notional": 10, "forced_sell_notional": 20}},
        "derivatives": {"open_interest": 1000, "open_interest_change": -.01,
                        "funding_rate": .0001, "basis_bps": 2.0},
        "market_state": "BUY_PRESSURE", "execution_authority": False,
    }


def _sqlite(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(database, "USE_POSTGRES", False)
    monkeypatch.setattr(database, "REQUIRE_PERSISTENT_DB", False)
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "DATABASE_NAME", tmp_path / "app.sqlite3")
    database.create_tables()


def test_frozen_candidate_identity_is_exact() -> None:
    candidate_ids, digest = candidate_identity()
    assert candidate_ids == EXPECTED_CANDIDATE_IDS
    assert len(candidate_ids) == 10
    assert len(digest) == 64


def test_shared_current_state_and_health_are_bounded(monkeypatch, tmp_path: Path) -> None:
    _sqlite(monkeypatch, tmp_path)
    repository = ForwardRuntimeStateRepository()
    digest = repository.register_identity()
    snapshot = _snapshot()
    cross = {"status": "VALID", "venue_count": 2, "dispersion_bps": 1.2}
    repository.publish_snapshot(snapshot, cross)
    repository.heartbeat(
        instance_id="test-instance", state="RUNNING",
        started_at="2026-09-20T12:00:00+00:00", candidate_identity_hash=digest,
        venues={"BINANCE": {"last_error": None}}, storage={"free_bytes": 50_000},
        last_event_at="2026-09-20T12:01:00+00:00",
    )
    rows = repository.latest_states(("BTCUSDT",))
    health = repository.health()
    assert len(rows) == 1
    assert rows[0]["snapshot"]["cross_venue"]["dispersion_bps"] == 1.2
    assert health and health["execution_authority"] is False
    assert health["candidate_identity_hash"] == digest


def test_terminal_views_do_not_disclose_candidate_performance() -> None:
    row = {
        "venue": "BINANCE", "symbol": "BTCUSDT", "data_quality": "VALID",
        "observed_at": "2026-09-20T12:00:00+00:00",
        "snapshot": _snapshot() | {"cross_venue": {"dispersion_bps": 1.2}},
    }
    market = render_market_overview([row])
    orderflow = render_order_flow([row], "BTCUSDT")
    shadow = render_shadow_status({
        "state": "RUNNING", "heartbeat_at": "2026-09-20T12:00:00+00:00",
        "last_event_at": "2026-09-20T12:00:00+00:00", "gap_count": 1,
    })
    assert "100,000.00" in market
    assert "BINANCE" in orderflow
    assert "10 frozen" in shadow
    assert "Candidate WR, PF, expectancy and PnL remain hidden" in shadow


def test_partitioned_raw_storage_is_manifested_and_replayable(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    store = AppendOnlyEventStore(
        tmp_path / "metadata.sqlite3", raw_partition_root=root, minimum_free_bytes=0,
    )
    event = RawMarketEvent(
        venue=Venue.BINANCE, market="USDT_PERPETUAL", symbol="BTCUSDT",
        instrument_type="PERPETUAL", event_type=EventType.TRADE,
        exchange_ts_ms=1_789_903_850_000, receive_ts_ms=1_789_903_850_001,
        payload={"p": "100000", "q": "0.1"}, price=100_000, quantity=.1,
        side="BUY",
    )
    receipt = store.append(event)
    assert receipt["duplicate"] is False
    assert store.counts()["raw_events"] == 0
    store.close()
    records = list(iter_partition_records(root))
    manifests = list(root.rglob("*.manifest.json"))
    assert len(records) == 1
    assert records[0]["semantic_id"] == event.semantic_id
    assert len(manifests) == 1
    assert json.loads(manifests[0].read_text(encoding="utf-8"))["event_count"] == 1


def test_render_blueprint_separates_app_and_single_disk_worker() -> None:
    text = Path("render.yaml").read_text(encoding="utf-8")
    assert text.count("type: web") == 1
    assert text.count("type: worker") == 1
    assert "name: liquidityvision-forward-worker" in text
    assert "startCommand: python -m tools.run_forward_microstructure_collector" in text
    assert "mountPath: /var/data" in text
    assert "FORWARD_COLLECTION_ENABLED" in text
    assert "FORWARD_PREVIOUS_EVIDENCE_END_UTC" in text
    assert text.count("LIVE_EXECUTION_ENABLED") == 2
    assert "value: SHADOW" in text


def test_restart_checkpoint_creates_explicit_non_replayable_gap(monkeypatch, tmp_path: Path) -> None:
    from tools.run_forward_microstructure_collector import _record_startup_gaps

    monkeypatch.delenv("FORWARD_PREVIOUS_EVIDENCE_END_UTC", raising=False)
    store = AppendOnlyEventStore(tmp_path / "metadata.sqlite3")
    store.checkpoint(
        recorded_ts_ms=1_000, venue="BINANCE", symbol="BTCUSDT",
        connection_id="old", last_sequence=10, state="BOOK_VALID", details={},
    )

    class Shared:
        gaps: list[dict] = []

        def record_gap(self, **kwargs):
            self.gaps.append(kwargs)

    shared = Shared()
    boundary = _record_startup_gaps(
        store, shared, ("BINANCE",), ("BTCUSDT",), 2_000,
    )
    assert boundary is not None
    assert store.counts()["gap_records"] == 1
    assert shared.gaps[0]["reason"] == "PROCESS_RESTART_GAP"
