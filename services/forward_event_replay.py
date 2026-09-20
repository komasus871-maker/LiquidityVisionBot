"""Deterministic receive-order replay for forward-collected raw events."""
from __future__ import annotations

import json
import heapq
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from services.forward_event_store import (
    AppendOnlyEventStore, EventType, IntegrityStatus, RawMarketEvent, Venue, decode_raw_payload,
)
from services.forward_microstructure_engine import CrossVenueState, MicrostructureFeatureEngine
from services.forward_partition_store import iter_partition_records
from services.forward_shadow_lab import ForwardOutcomeLabeler, ForwardShadowEngine


def _event_from_record(record: dict) -> RawMarketEvent:
    return RawMarketEvent(
        venue=Venue(record["venue"]), market=record["market"], symbol=record["symbol"],
        instrument_type=record["instrument_type"], event_type=EventType(record["event_type"]),
        exchange_ts_ms=int(record["exchange_ts_ms"]), receive_ts_ms=int(record["receive_ts_ms"]),
        sequence_start=record.get("sequence_start"), sequence_end=record.get("sequence_end"),
        previous_sequence=record.get("previous_sequence"), price=record.get("price"),
        quantity=record.get("quantity"), side=record.get("side"),
        is_buyer_maker=record.get("is_buyer_maker"), connection_id=record.get("connection_id"),
        payload=record["payload"], integrity_status=IntegrityStatus(record["integrity_status"]),
        schema_version=record["schema_version"], metadata=record.get("metadata") or {},
    )


def iter_raw_events(path: str | Path, raw_partition_root: str | Path | None = None) -> Iterator[RawMarketEvent]:
    """Yield first-seen events in their original local ingestion order."""
    def legacy_items() -> Iterator[tuple[int, RawMarketEvent]]:
        connection = sqlite3.connect(Path(path))
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                "SELECT * FROM raw_events WHERE duplicate_of IS NULL ORDER BY receive_ts_ms,ingest_id"
            )
            for row in rows:
                yield int(row["receive_ts_ms"]) * 1_000_000 + int(row["ingest_id"]), RawMarketEvent(
                    venue=Venue(row["venue"]), market=row["market"], symbol=row["symbol"],
                    instrument_type=row["instrument_type"], event_type=EventType(row["event_type"]),
                    exchange_ts_ms=int(row["exchange_ts_ms"]), receive_ts_ms=int(row["receive_ts_ms"]),
                    sequence_start=row["sequence_start"], sequence_end=row["sequence_end"],
                    previous_sequence=row["previous_sequence"], price=row["price"], quantity=row["quantity"],
                    side=row["side"], is_buyer_maker=(
                        None if row["is_buyer_maker"] is None else bool(row["is_buyer_maker"])
                    ),
                    payload=decode_raw_payload(row["raw_json"], row["raw_encoding"]),
                    connection_id=row["connection_id"], integrity_status=IntegrityStatus(row["integrity_status"]),
                    schema_version=row["schema_version"], metadata=json.loads(row["metadata_json"]),
                )
        finally:
            connection.close()

    def partition_items() -> Iterator[tuple[int, RawMarketEvent]]:
        if self_root := raw_partition_root:
            for record in iter_partition_records(self_root):
                if not record.get("duplicate"):
                    yield int(record["ingest_order_ns"]), _event_from_record(record)

    for _, event in heapq.merge(legacy_items(), partition_items(), key=lambda item: item[0]):
        yield event


def _ids(path: Path, table: str, column: str) -> set[str]:
    with sqlite3.connect(path) as connection:
        return {str(row[0]) for row in connection.execute(f"SELECT {column} FROM {table}")}


@dataclass(frozen=True)
class ReplayResult:
    events_replayed: int
    resync_intervals: int
    counts: dict[str, int]
    decision_ids_match: bool
    feature_ids_match: bool
    execution_authority: bool = False


class ForwardEventReplay:
    """Rebuild derived snapshots and Shadow decisions without touching the source ledger."""

    def __init__(self, source: str | Path, output: str | Path, *,
                 raw_partition_root: str | Path | None = None, feature_interval_ms: int = 1_000):
        self.source = Path(source)
        self.output = Path(output)
        self.raw_partition_root = Path(raw_partition_root) if raw_partition_root else None
        self.feature_interval_ms = max(100, feature_interval_ms)

    def run(self) -> ReplayResult:
        if not self.source.is_file():
            raise FileNotFoundError(self.source)
        if self.output.exists():
            raise FileExistsError(f"replay output already exists: {self.output}")
        store = AppendOnlyEventStore(self.output)
        engine = MicrostructureFeatureEngine()
        cross = CrossVenueState()
        shadow = ForwardShadowEngine(store)
        labeler = ForwardOutcomeLabeler(store)
        last_feature_ms: dict[tuple[str, str], int] = {}
        events_replayed = 0
        resync_intervals = 0

        for event in iter_raw_events(self.source, self.raw_partition_root):
            events_replayed += 1
            result = engine.ingest(event, include_snapshot=False)
            if result["resync_required"]:
                resync_intervals += 1
                continue
            key = event.venue.value, event.symbol
            if event.receive_ts_ms - last_feature_ms.get(key, 0) < self.feature_interval_ms:
                continue
            last_feature_ms[key] = event.receive_ts_ms
            snapshot = engine.snapshot(event.venue.value, event.symbol, event.receive_ts_ms)
            cross.update(snapshot)
            cross_features = cross.features(event.symbol, event.receive_ts_ms)
            store.append_feature(snapshot | {"cross_venue": cross_features})
            decisions = shadow.evaluate(snapshot, cross_features)
            mid = snapshot.get("book", {}).get("mid")
            if mid:
                for decision in decisions:
                    labeler.register(decision, float(mid))
                labeler.observe(
                    venue=event.venue.value, symbol=event.symbol,
                    observed_ts_ms=event.receive_ts_ms, mid=float(mid), book=snapshot["book"],
                )

        source_decisions = _ids(self.source, "shadow_decisions", "decision_id")
        replay_decisions = _ids(self.output, "shadow_decisions", "decision_id")
        source_features = _ids(self.source, "feature_snapshots", "snapshot_id")
        replay_features = _ids(self.output, "feature_snapshots", "snapshot_id")
        return ReplayResult(
            events_replayed=events_replayed, resync_intervals=resync_intervals,
            counts=store.counts(), decision_ids_match=source_decisions == replay_decisions,
            feature_ids_match=source_features == replay_features,
        )
