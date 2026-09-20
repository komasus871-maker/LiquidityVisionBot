"""Crash-tolerant partitioned raw-event storage for the forward lab."""
from __future__ import annotations

import hashlib
import heapq
import json
import os
import shutil
import struct
import time
import uuid
import zlib
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from services.forward_event_store import RawMarketEvent, canonical_json


MAGIC = b"LVFWD2\n"
FRAME_HEADER = struct.Struct(">II")
PARTITION_SCHEMA = "forward-raw-partition-v1"


class StorageUnsafeError(RuntimeError):
    pass


@dataclass
class _Segment:
    path: Path
    handle: Any
    digest: Any
    event_count: int = 0
    byte_count: int = 0
    first_receive_ts_ms: int | None = None
    last_receive_ts_ms: int | None = None
    last_fsync_monotonic: float = 0.0


class PartitionedRawLedger:
    """Writes independently recoverable compressed frames to deterministic partitions."""

    def __init__(self, root: str | Path, *, minimum_free_bytes: int = 10 * 1024**3,
                 fsync_interval_seconds: float = 1.0, duplicate_window: int = 500_000):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.minimum_free_bytes = int(minimum_free_bytes)
        self.fsync_interval_seconds = float(fsync_interval_seconds)
        self.duplicate_window = int(duplicate_window)
        self.session_id = f"{time.time_ns()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self._segments: dict[tuple[str, str, str, str], _Segment] = {}
        self._seen: OrderedDict[str, int] = OrderedDict()
        self._last_order = 0
        self.event_count = 0
        self.duplicate_count = 0
        self.bytes_written = 0
        self.write_latency_ms: list[float] = []
        self.last_write_ts_ms: int | None = None
        self._closed = False

    @staticmethod
    def _date(receive_ts_ms: int) -> str:
        return datetime.fromtimestamp(receive_ts_ms / 1000, tz=timezone.utc).date().isoformat()

    def _open_segment(self, event: RawMarketEvent) -> _Segment:
        key = (self._date(event.receive_ts_ms), event.venue.value, event.symbol, event.event_type.value)
        existing = self._segments.get(key)
        if existing:
            return existing
        directory = self.root.joinpath(*key)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"segment-{self.session_id}.fwdz"
        handle = path.open("xb", buffering=0)
        handle.write(MAGIC)
        digest = hashlib.sha256()
        digest.update(MAGIC)
        segment = _Segment(path=path, handle=handle, digest=digest, byte_count=len(MAGIC))
        self._segments[key] = segment
        return segment

    def _check_space(self) -> int:
        free = int(shutil.disk_usage(self.root).free)
        if free < self.minimum_free_bytes:
            raise StorageUnsafeError(
                f"free disk {free} below forward-lab safety floor {self.minimum_free_bytes}"
            )
        return free

    def append(self, event: RawMarketEvent) -> dict[str, Any]:
        if self._closed:
            raise RuntimeError("raw partition ledger is closed")
        if self.event_count % 1_000 == 0:
            self._check_space()
        started = time.perf_counter_ns()
        order = max(time.time_ns(), self._last_order + 1)
        self._last_order = order
        duplicate_of = self._seen.get(event.semantic_id)
        self._seen[event.semantic_id] = order
        self._seen.move_to_end(event.semantic_id)
        while len(self._seen) > self.duplicate_window:
            self._seen.popitem(last=False)
        record = event.record() | {
            "partition_schema": PARTITION_SCHEMA,
            "ingest_order_ns": order,
            "duplicate": duplicate_of is not None,
            "duplicate_of_ingest_order_ns": duplicate_of,
        }
        compressed = zlib.compress(canonical_json(record).encode("utf-8"), level=6)
        header = FRAME_HEADER.pack(len(compressed), zlib.crc32(compressed) & 0xFFFFFFFF)
        segment = self._open_segment(event)
        try:
            segment.handle.write(header)
            segment.handle.write(compressed)
            segment.handle.flush()
            now_monotonic = time.monotonic()
            if now_monotonic - segment.last_fsync_monotonic >= self.fsync_interval_seconds:
                os.fsync(segment.handle.fileno())
                segment.last_fsync_monotonic = now_monotonic
        except OSError as exc:
            raise StorageUnsafeError(f"raw partition write failed: {exc}") from exc
        segment.digest.update(header)
        segment.digest.update(compressed)
        segment.event_count += 1
        segment.byte_count += len(header) + len(compressed)
        segment.first_receive_ts_ms = segment.first_receive_ts_ms or event.receive_ts_ms
        segment.last_receive_ts_ms = event.receive_ts_ms
        self.event_count += 1
        self.duplicate_count += int(duplicate_of is not None)
        self.bytes_written += len(header) + len(compressed)
        self.last_write_ts_ms = event.receive_ts_ms
        self.write_latency_ms.append((time.perf_counter_ns() - started) / 1_000_000)
        if len(self.write_latency_ms) > 10_000:
            del self.write_latency_ms[:5_000]
        return {
            "ingest_id": order, "semantic_id": event.semantic_id,
            "duplicate": duplicate_of is not None, "duplicate_of": duplicate_of,
            "partition": str(segment.path.relative_to(self.root)),
        }

    def close(self) -> None:
        if self._closed:
            return
        for segment in self._segments.values():
            try:
                segment.handle.flush()
                os.fsync(segment.handle.fileno())
            finally:
                segment.handle.close()
            manifest = {
                "schema": PARTITION_SCHEMA, "path": segment.path.name,
                "sha256": segment.digest.hexdigest(), "event_count": segment.event_count,
                "byte_count": segment.byte_count,
                "start_receive_ts_ms": segment.first_receive_ts_ms,
                "end_receive_ts_ms": segment.last_receive_ts_ms,
                "finalized_at_utc": datetime.now(timezone.utc).isoformat(),
            }
            target = segment.path.with_suffix(".manifest.json")
            temporary = target.with_suffix(".tmp")
            temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            os.replace(temporary, target)
        self._closed = True

    def health(self) -> dict[str, Any]:
        latencies = sorted(self.write_latency_ms)
        percentile = lambda q: latencies[min(len(latencies) - 1, int((len(latencies) - 1) * q))] if latencies else None
        return {
            "schema": PARTITION_SCHEMA, "root": str(self.root), "session_id": self.session_id,
            "events": self.event_count, "duplicates": self.duplicate_count,
            "bytes_written": self.bytes_written, "last_write_ts_ms": self.last_write_ts_ms,
            "active_segments": len(self._segments), "free_bytes": shutil.disk_usage(self.root).free,
            "minimum_free_bytes": self.minimum_free_bytes,
            "write_latency_ms": {"p50": percentile(.50), "p95": percentile(.95), "p99": percentile(.99)},
        }


def read_segment(path: str | Path, *, require_manifest: bool = False) -> Iterator[dict[str, Any]]:
    source = Path(path)
    manifest_path = source.with_suffix(".manifest.json")
    if require_manifest and not manifest_path.is_file():
        raise StorageUnsafeError(f"unfinalized partition: {source}")
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        actual = hashlib.sha256(source.read_bytes()).hexdigest()
        if actual != manifest["sha256"]:
            raise StorageUnsafeError(f"partition hash mismatch: {source}")
    with source.open("rb") as handle:
        if handle.read(len(MAGIC)) != MAGIC:
            raise StorageUnsafeError(f"partition magic mismatch: {source}")
        while True:
            header = handle.read(FRAME_HEADER.size)
            if not header:
                return
            if len(header) != FRAME_HEADER.size:
                raise StorageUnsafeError(f"truncated partition header: {source}")
            length, expected_crc = FRAME_HEADER.unpack(header)
            payload = handle.read(length)
            if len(payload) != length:
                raise StorageUnsafeError(f"truncated partition frame: {source}")
            if zlib.crc32(payload) & 0xFFFFFFFF != expected_crc:
                raise StorageUnsafeError(f"partition CRC mismatch: {source}")
            yield json.loads(zlib.decompress(payload).decode("utf-8"))


def iter_partition_records(root: str | Path) -> Iterator[dict[str, Any]]:
    iterators = [iter(read_segment(path)) for path in sorted(Path(root).rglob("*.fwdz"))]
    heap: list[tuple[int, int, dict[str, Any], Iterator[dict[str, Any]]]] = []
    for index, iterator in enumerate(iterators):
        try:
            record = next(iterator)
        except StopIteration:
            continue
        heap.append((int(record["ingest_order_ns"]), index, record, iterator))
    heapq.heapify(heap)
    while heap:
        _, index, record, iterator = heapq.heappop(heap)
        yield record
        try:
            following = next(iterator)
        except StopIteration:
            continue
        heapq.heappush(heap, (int(following["ingest_order_ns"]), index, following, iterator))
