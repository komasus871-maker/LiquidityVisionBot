from __future__ import annotations

import json
import os
import struct
from pathlib import Path

import pytest

import database.database as database
from services.forward_evidence_archive import (
    ForwardEvidenceArchive, RemotePartitionReplay, reconstruct_registry_from_remote,
)
from services.forward_event_replay import ForwardEventReplay
from services.forward_event_store import AppendOnlyEventStore, EventType, RawMarketEvent, Venue
from services.forward_object_storage import (
    InMemoryObjectStorage, ObjectIntegrityError, ObjectStorageError,
)
from services.forward_partition_store import PartitionedRawLedger, iter_partition_records
from services.forward_runtime_state import ForwardRuntimeStateRepository, candidate_identity


def _event(ts: int = 1_789_903_850_001, price: float = 100_000) -> RawMarketEvent:
    return RawMarketEvent(
        venue=Venue.BINANCE, market="USDT_PERPETUAL", symbol="BTCUSDT",
        instrument_type="PERPETUAL", event_type=EventType.TRADE,
        exchange_ts_ms=ts - 1, receive_ts_ms=ts,
        payload={"p": str(price), "q": "0.1"}, price=price, quantity=.1, side="BUY",
    )


def _sealed(tmp_path: Path, *events: RawMarketEvent) -> tuple[Path, Path]:
    root = tmp_path / "raw"
    store = AppendOnlyEventStore(
        tmp_path / "metadata.sqlite3", raw_partition_root=root, minimum_free_bytes=0,
    )
    for event in events or (_event(),):
        store.append(event)
    store.close()
    return root, next(root.rglob("*.manifest.json"))


def test_seal_upload_verify_and_only_then_evict(tmp_path: Path) -> None:
    root, manifest_path = _sealed(tmp_path)
    backend = InMemoryObjectStorage()
    archive = ForwardEvidenceArchive(
        root, backend, local_cache_bytes=0, minimum_free_bytes=0,
    )
    result = archive.process_manifest(manifest_path)
    source = next(root.rglob("*.fwdz"))
    assert result.state == "REMOTE_VERIFIED" and source.exists()
    assert len(backend.objects) == 2  # immutable evidence plus remote manifest
    removed = archive.evict_verified(force_to_cache_limit=True)
    assert removed > 0 and not source.exists()
    state = json.loads(archive._state_path(manifest_path).read_text(encoding="utf-8"))
    assert state["local_state"] == "EVICTED_REMOTE_VERIFIED"


def test_interrupted_upload_is_retryable_and_restart_recovers(tmp_path: Path) -> None:
    root, manifest_path = _sealed(tmp_path)
    backend = InMemoryObjectStorage()
    backend.interrupt_next_upload = True
    first = ForwardEvidenceArchive(root, backend, minimum_free_bytes=0)
    with pytest.raises(ObjectStorageError):
        first.process_manifest(manifest_path)
    assert next(root.rglob("*.fwdz")).exists()
    second = ForwardEvidenceArchive(root, backend, minimum_free_bytes=0)
    results = second.process_pending(force=True)
    assert results and results[0].state == "REMOTE_VERIFIED"


def test_restart_seals_complete_frames_and_discards_only_partial_tail(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    ledger = PartitionedRawLedger(root, minimum_free_bytes=0)
    ledger.append(_event())
    segment = next(iter(ledger._segments.values()))
    segment.handle.write(struct.pack(">II", 100, 0))
    segment.handle.write(b"partial")
    segment.handle.flush()
    os.fsync(segment.handle.fileno())
    segment.handle.close()
    recovered = PartitionedRawLedger(root, minimum_free_bytes=0)
    assert recovered.recovered_segments == 1
    manifest = json.loads(next(root.rglob("*.manifest.json")).read_text(encoding="utf-8"))
    assert manifest["recovered_after_restart"] is True
    assert manifest["event_count"] == 1
    assert len(list(iter_partition_records(root))) == 1


def test_duplicate_is_idempotent_but_same_key_different_checksum_fails_closed(tmp_path: Path) -> None:
    root, manifest_path = _sealed(tmp_path)
    backend = InMemoryObjectStorage()
    archive = ForwardEvidenceArchive(root, backend, minimum_free_bytes=0)
    first = archive.process_manifest(manifest_path)
    puts = backend.put_attempts
    second = archive.process_manifest(manifest_path)
    assert first.object_key == second.object_key and backend.put_attempts == puts + 2
    payload, metadata, modified = backend.objects[first.object_key]
    backend.objects[first.object_key] = (payload + b"x", metadata | {"sha256": "0" * 64}, modified)
    with pytest.raises(ObjectIntegrityError):
        archive.process_manifest(manifest_path)
    assert next(root.rglob("*.fwdz")).exists()


def test_outage_keeps_local_source_and_reports_degraded_spool(tmp_path: Path) -> None:
    root, manifest_path = _sealed(tmp_path)
    backend = InMemoryObjectStorage()
    backend.available = False
    archive = ForwardEvidenceArchive(root, backend, minimum_free_bytes=0)
    assert archive.process_pending(force=True) == []
    health = archive.health()
    assert health["object_storage_status"] == "DEGRADED"
    assert health["pending_partitions"] == 1
    assert health["pending_upload_bytes"] > 0
    assert next(root.rglob("*.fwdz")).exists()


def test_remote_replay_downloads_one_bucket_validates_and_deduplicates(tmp_path: Path) -> None:
    duplicate = _event()
    root, manifest_path = _sealed(tmp_path, duplicate, duplicate, _event(price=100_001))
    backend = InMemoryObjectStorage()
    archive = ForwardEvidenceArchive(root, backend, minimum_free_bytes=0)
    uploaded = archive.process_manifest(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    registry = [manifest | {"object_key": uploaded.object_key}]
    records = list(RemotePartitionReplay(backend, tmp_path / "cache").records(registry))
    assert len(records) == 3
    assert sum(not row["duplicate"] for row in records) == 2
    assert list((tmp_path / "cache").rglob("*.fwdz")) == []


def test_remote_manifest_reconstructs_registry_and_full_replay_stream(tmp_path: Path) -> None:
    root, manifest_path = _sealed(tmp_path)
    backend = InMemoryObjectStorage()
    archive = ForwardEvidenceArchive(root, backend, minimum_free_bytes=0)
    archive.process_manifest(manifest_path)
    registry = list(reconstruct_registry_from_remote(backend, tmp_path / "manifest-cache"))
    assert len(registry) == 1 and registry[0]["state"] == "REMOTE_VERIFIED"
    result = ForwardEventReplay(
        tmp_path / "missing-local-metadata.sqlite3", tmp_path / "replayed.sqlite3",
        remote_registry=registry, object_backend=backend,
        cache_root=tmp_path / "replay-cache", feature_interval_ms=100,
    ).run()
    assert result.events_replayed == 1
    assert result.decision_ids_match and result.feature_ids_match


def test_remote_replay_rejects_corruption(tmp_path: Path) -> None:
    root, manifest_path = _sealed(tmp_path)
    backend = InMemoryObjectStorage()
    archive = ForwardEvidenceArchive(root, backend, minimum_free_bytes=0)
    uploaded = archive.process_manifest(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload, metadata, modified = backend.objects[uploaded.object_key]
    backend.objects[uploaded.object_key] = (payload[:-1] + b"x", metadata, modified)
    with pytest.raises(ObjectIntegrityError):
        list(RemotePartitionReplay(backend, tmp_path / "cache").records([
            manifest | {"object_key": uploaded.object_key}
        ]))


def test_bounded_remote_audit_reports_missing_object_without_evicting(tmp_path: Path) -> None:
    root, manifest_path = _sealed(tmp_path)
    backend = InMemoryObjectStorage()
    archive = ForwardEvidenceArchive(root, backend, minimum_free_bytes=0)
    result = archive.process_manifest(manifest_path)
    del backend.objects[result.object_key]
    assert archive.audit_remote(limit=1) == 1
    health = archive.health()
    assert health["missing_remote_objects"] == 1
    assert health["object_storage_status"] == "DEGRADED"
    assert next(root.rglob("*.fwdz")).exists()


def test_remote_retention_is_explicit_and_protected_by_default(tmp_path: Path) -> None:
    root, manifest_path = _sealed(tmp_path)
    backend = InMemoryObjectStorage()
    archive = ForwardEvidenceArchive(root, backend, minimum_free_bytes=0)
    uploaded = archive.process_manifest(manifest_path)
    with pytest.raises(PermissionError):
        archive.release_remote(manifest_path)
    with pytest.raises(Exception, match="protected"):
        archive.release_remote(manifest_path, authorized=True)
    assert uploaded.object_key in backend.objects


def test_registry_is_compact_metadata_without_raw_payload(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(database, "USE_POSTGRES", False)
    monkeypatch.setattr(database, "REQUIRE_PERSISTENT_DB", False)
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "DATABASE_NAME", tmp_path / "application.sqlite3")
    database.create_tables()
    repository = ForwardRuntimeStateRepository()
    root, manifest_path = _sealed(tmp_path / "ledger")
    backend = InMemoryObjectStorage()
    archive = ForwardEvidenceArchive(
        root, backend, minimum_free_bytes=0, registry_sink=repository.register_partition,
    )
    archive.process_manifest(manifest_path)
    rows = repository.replay_partitions()
    assert len(rows) == 1 and rows[0]["replay_available"] == 1
    with database.connect() as connection:
        columns = {
            row[1] for row in connection.execute(
                "PRAGMA table_info(forward_partition_registry)"
            ).fetchall()
        }
    assert "raw_json" not in columns and "payload" not in columns


def test_metadata_compaction_keeps_unresolved_and_recent_but_raw_identity_is_unchanged(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    store = AppendOnlyEventStore(
        tmp_path / "metadata.sqlite3", raw_partition_root=root, minimum_free_bytes=0,
    )
    store.append(_event())
    base = {
        "schema_version": "forward-microstructure-feature-v1", "receive_ts_ms": 1_000,
        "venue": "BINANCE", "symbol": "BTCUSDT", "data_quality": {"status": "VALID"},
    }
    old_id = store.append_feature(base | {"timestamp_ms": 1_000})
    unresolved_id = store.append_feature(base | {"timestamp_ms": 2_000, "receive_ts_ms": 2_000})
    recent_id = store.append_feature(base | {"timestamp_ms": 20_000, "receive_ts_ms": 20_000})
    for decision_id, snapshot_id, timestamp in (
        ("resolved", old_id, 1_000), ("unresolved", unresolved_id, 2_000),
    ):
        store.append_shadow_decision({
            "decision_id": decision_id, "candidate_id": candidate_identity()[0][0],
            "family": "M1_FLOW_BOOK_MOMENTUM", "direction": "LONG",
            "decision_ts_ms": timestamp, "first_evidence_ts_ms": timestamp,
            "venue": "BINANCE", "symbol": "BTCUSDT", "feature_snapshot_id": snapshot_id,
            "execution_authority": False,
        })
    for horizon in range(9):
        store.append_label({
            "decision_id": "resolved", "horizon_ms": horizon,
            "observed_ts_ms": 3_000 + horizon,
        })
    before_identity = candidate_identity()
    report = store.compact_rebuildable_metadata(
        retain_after_ts_ms=10_000, remote_verified_through_ts_ms=20_000,
    )
    assert report["after"]["shadow_decisions"] == 1
    assert report["after"]["feature_snapshots"] == 2
    assert store.unresolved_shadow_decisions(range(9))[0]["decision"]["decision_id"] == "unresolved"
    assert candidate_identity() == before_identity
    assert recent_id
    assert not list(tmp_path.glob("*.compact*"))
    with store._connect() as connection:
        with pytest.raises(Exception, match="APPEND_ONLY"):
            connection.execute("DELETE FROM shadow_decisions")


def test_metadata_compaction_refuses_unverified_raw_cutoff(tmp_path: Path) -> None:
    store = AppendOnlyEventStore(
        tmp_path / "metadata.sqlite3", raw_partition_root=tmp_path / "raw",
        minimum_free_bytes=0,
    )
    with pytest.raises(RuntimeError, match="does not cover"):
        store.compact_rebuildable_metadata(
            retain_after_ts_ms=10_000, remote_verified_through_ts_ms=9_999,
        )


def test_live_flags_and_worker_authority_remain_fail_closed() -> None:
    text = Path("render.yaml").read_text(encoding="utf-8")
    for flag in (
        "LIVE_EXECUTION_ENABLED", "LIVE_DISPATCHER_ENABLED",
        "ALLOW_USER_LIVE_CONNECTIONS", "BINGX_PRODUCTION_ADAPTER_ALLOWED",
    ):
        assert text.count(flag) == 3
    worker = text.split("name: liquidityvision-forward-worker", 1)[1]
    assert "EXECUTION_MODE\n        value: SHADOW" in worker
    assert "sizeGB: 50" in worker
