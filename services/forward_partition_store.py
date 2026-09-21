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
PARTITION_SCHEMA = "forward-raw-partition-v2"
GIB = 1024 ** 3
MEASURED_FORWARD_BYTES_PER_DAY = 13_840_635_579


def disk_health(
    *, total_bytes: int, used_bytes: int, free_bytes: int,
    growth_bytes_per_day: float | None, minimum_free_bytes: int,
    warning_usage_percent: float = 75.0, critical_usage_percent: float = 90.0,
    warning_free_gb: float = 100.0, critical_free_gb: float = 10.0,
    warning_days: float = 7.0, critical_days: float = 3.0,
) -> dict[str, Any]:
    """Return explicit capacity state without altering retention or write fidelity."""
    total = max(0, int(total_bytes))
    used = max(0, int(used_bytes))
    free = max(0, int(free_bytes))
    rate = max(0.0, float(growth_bytes_per_day or 0.0))
    usage_percent = (used / total * 100.0) if total else None
    usable_free = max(0, free - max(0, int(minimum_free_bytes)))
    days_remaining = (usable_free / rate) if rate > 0 else None
    critical = (
        (usage_percent is not None and usage_percent >= critical_usage_percent)
        or free <= critical_free_gb * GIB
        or (days_remaining is not None and days_remaining <= critical_days)
    )
    warning = critical or (
        (usage_percent is not None and usage_percent >= warning_usage_percent)
        or free <= warning_free_gb * GIB
        or (days_remaining is not None and days_remaining <= warning_days)
    )
    return {
        "disk_status": "CRITICAL" if critical else "WARNING" if warning else "HEALTHY",
        "total_bytes": total, "used_bytes": used, "free_bytes": free,
        "usage_percent": round(usage_percent, 2) if usage_percent is not None else None,
        "free_gb": round(free / GIB, 2),
        "growth_bytes_per_day": round(rate) if rate else None,
        "estimated_days_remaining": round(days_remaining, 2) if days_remaining is not None else None,
        "thresholds": {
            "warning_usage_percent": warning_usage_percent,
            "critical_usage_percent": critical_usage_percent,
            "warning_free_gb": warning_free_gb,
            "critical_free_gb": critical_free_gb,
            "warning_days_remaining": warning_days,
            "critical_days_remaining": critical_days,
            "hard_minimum_free_bytes": int(minimum_free_bytes),
        },
    }


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
    first_exchange_ts_ms: int | None = None
    last_exchange_ts_ms: int | None = None
    bucket_start_ts_ms: int = 0
    venue: str = ""
    symbol: str = ""
    event_type: str = ""
    last_fsync_monotonic: float = 0.0


class PartitionedRawLedger:
    """Writes independently recoverable compressed frames to deterministic partitions."""

    def __init__(self, root: str | Path, *, minimum_free_bytes: int = 10 * 1024**3,
                 fsync_interval_seconds: float = 1.0, duplicate_window: int = 500_000,
                 max_segment_seconds: int = 300, collector_version: str | None = None,
                 program_identity: str | None = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.minimum_free_bytes = int(minimum_free_bytes)
        self.fsync_interval_seconds = float(fsync_interval_seconds)
        self.duplicate_window = int(duplicate_window)
        self.max_segment_ms = max(60, int(max_segment_seconds)) * 1_000
        self.collector_version = collector_version or os.getenv("APP_VERSION", "unknown")
        self.program_identity = program_identity or "forward-microstructure-alpha-v1"
        self.session_id = f"{time.time_ns()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self._segments: dict[tuple[int, str, str, str], _Segment] = {}
        self._seen: OrderedDict[str, int] = OrderedDict()
        self._last_order = 0
        self._started_monotonic = time.monotonic()
        self._initial_used_bytes = int(shutil.disk_usage(self.root).used)
        self.event_count = 0
        self.duplicate_count = 0
        self.bytes_written = 0
        self.write_latency_ms: list[float] = []
        self.last_write_ts_ms: int | None = None
        self._closed = False
        self.recovered_segments = self._recover_unsealed_segments()

    def _recover_unsealed_segments(self) -> int:
        """Seal every fsynced complete frame left by a terminated worker."""
        recovered = 0
        for path in sorted(self.root.rglob("*.fwdz")):
            manifest_path = path.with_suffix(".manifest.json")
            if manifest_path.is_file():
                continue
            first_record: dict[str, Any] | None = None
            last_record: dict[str, Any] | None = None
            event_count = 0
            venues: set[str] = set()
            symbols: set[str] = set()
            event_types: set[str] = set()
            last_good = len(MAGIC)
            with path.open("rb") as handle:
                if handle.read(len(MAGIC)) != MAGIC:
                    raise StorageUnsafeError(f"orphan partition magic mismatch: {path}")
                while True:
                    header = handle.read(FRAME_HEADER.size)
                    if not header:
                        break
                    if len(header) != FRAME_HEADER.size:
                        break
                    length, expected_crc = FRAME_HEADER.unpack(header)
                    payload = handle.read(length)
                    if len(payload) != length:
                        break
                    if zlib.crc32(payload) & 0xFFFFFFFF != expected_crc:
                        raise StorageUnsafeError(f"orphan partition CRC mismatch: {path}")
                    try:
                        record = json.loads(zlib.decompress(payload).decode("utf-8"))
                    except (ValueError, zlib.error, UnicodeDecodeError) as exc:
                        raise StorageUnsafeError(f"orphan partition payload invalid: {path}") from exc
                    first_record = first_record or record
                    last_record = record
                    event_count += 1
                    venues.add(str(record["venue"]))
                    symbols.add(str(record["symbol"]))
                    event_types.add(str(record["event_type"]))
                    last_good = handle.tell()
            if not event_count or first_record is None or last_record is None:
                path.rename(path.with_suffix(".empty-orphan"))
                continue
            if path.stat().st_size != last_good:
                with path.open("r+b") as handle:
                    handle.truncate(last_good)
                    handle.flush()
                    os.fsync(handle.fileno())
            if len(venues) != 1 or len(symbols) != 1 or len(event_types) != 1:
                raise StorageUnsafeError(f"orphan partition mixed stream identity: {path}")
            with path.open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
            first_receive = int(first_record["receive_ts_ms"])
            last_receive = int(last_record["receive_ts_ms"])
            first_exchange = int(first_record["exchange_ts_ms"])
            last_exchange = int(last_record["exchange_ts_ms"])
            bucket_start = first_receive - (first_receive % self.max_segment_ms)
            manifest = {
                "schema": PARTITION_SCHEMA, "partition_id": path.stem,
                "path": path.name, "relative_path": path.relative_to(self.root).as_posix(),
                "sha256": digest, "event_count": event_count,
                "byte_count": path.stat().st_size,
                "first_receive_ts_ms": first_receive, "last_receive_ts_ms": last_receive,
                "first_exchange_ts_ms": first_exchange, "last_exchange_ts_ms": last_exchange,
                "bucket_start_ts_ms": bucket_start, "venue": next(iter(venues)),
                "symbol": next(iter(symbols)), "event_types": sorted(event_types),
                "collector_version": self.collector_version,
                "program_identity": self.program_identity,
                "finalized_at_utc": datetime.now(timezone.utc).isoformat(),
                "immutable": True, "recovered_after_restart": True,
            }
            temporary = manifest_path.with_suffix(".tmp")
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, manifest_path)
            recovered += 1
        return recovered

    def _open_segment(self, event: RawMarketEvent) -> _Segment:
        bucket_start = event.receive_ts_ms - (event.receive_ts_ms % self.max_segment_ms)
        key = (bucket_start, event.venue.value, event.symbol, event.event_type.value)
        existing = self._segments.get(key)
        if existing:
            return existing
        instant = datetime.fromtimestamp(bucket_start / 1000, tz=timezone.utc)
        directory = self.root.joinpath(
            f"{instant.year:04d}", f"{instant.month:02d}", f"{instant.day:02d}",
            f"{instant.hour:02d}", event.venue.value, event.symbol, event.event_type.value,
        )
        directory.mkdir(parents=True, exist_ok=True)
        partition_id = (
            f"{bucket_start}-{event.venue.value.lower()}-{event.symbol.lower()}-"
            f"{event.event_type.value.lower()}-{self.session_id}"
        )
        path = directory / f"{partition_id}.fwdz"
        handle = path.open("xb", buffering=0)
        handle.write(MAGIC)
        digest = hashlib.sha256()
        digest.update(MAGIC)
        segment = _Segment(
            path=path, handle=handle, digest=digest, byte_count=len(MAGIC),
            bucket_start_ts_ms=bucket_start, venue=event.venue.value,
            symbol=event.symbol, event_type=event.event_type.value,
        )
        self._segments[key] = segment
        return segment

    def _seal_segment(self, key: tuple[int, str, str, str]) -> Path:
        segment = self._segments.pop(key)
        try:
            segment.handle.flush()
            os.fsync(segment.handle.fileno())
        finally:
            segment.handle.close()
        manifest = {
            "schema": PARTITION_SCHEMA,
            "partition_id": segment.path.stem,
            "path": segment.path.name,
            "relative_path": segment.path.relative_to(self.root).as_posix(),
            "sha256": segment.digest.hexdigest(),
            "event_count": segment.event_count,
            "byte_count": segment.byte_count,
            "first_receive_ts_ms": segment.first_receive_ts_ms,
            "last_receive_ts_ms": segment.last_receive_ts_ms,
            "first_exchange_ts_ms": segment.first_exchange_ts_ms,
            "last_exchange_ts_ms": segment.last_exchange_ts_ms,
            "bucket_start_ts_ms": segment.bucket_start_ts_ms,
            "venue": segment.venue,
            "symbol": segment.symbol,
            "event_types": [segment.event_type],
            "collector_version": self.collector_version,
            "program_identity": self.program_identity,
            "finalized_at_utc": datetime.now(timezone.utc).isoformat(),
            "immutable": True,
        }
        target = segment.path.with_suffix(".manifest.json")
        temporary = target.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        return target

    def seal_completed(self, observed_ts_ms: int | None = None) -> list[Path]:
        """Seal UTC-bucket partitions that cannot accept a current event."""
        boundary = int(observed_ts_ms if observed_ts_ms is not None else time.time_ns() // 1_000_000)
        ready = [key for key in self._segments if key[0] + self.max_segment_ms <= boundary]
        return [self._seal_segment(key) for key in sorted(ready)]

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
        segment.first_exchange_ts_ms = segment.first_exchange_ts_ms or event.exchange_ts_ms
        segment.last_exchange_ts_ms = event.exchange_ts_ms
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
        for key in sorted(tuple(self._segments)):
            self._seal_segment(key)
        self._closed = True

    def health(self) -> dict[str, Any]:
        latencies = sorted(self.write_latency_ms)
        percentile = lambda q: latencies[min(len(latencies) - 1, int((len(latencies) - 1) * q))] if latencies else None
        usage = shutil.disk_usage(self.root)
        elapsed = max(0.001, time.monotonic() - self._started_monotonic)
        observed_growth = None
        if elapsed >= 300:
            session_growth = max(
                self.bytes_written, int(usage.used) - self._initial_used_bytes, 0,
            )
            observed_growth = session_growth / elapsed * 86_400
        expected_growth = max(
            0, int(os.getenv(
                "FORWARD_EXPECTED_BYTES_PER_DAY", str(MEASURED_FORWARD_BYTES_PER_DAY),
            )),
        )
        growth = max(float(expected_growth), float(observed_growth or 0.0)) or None
        capacity = disk_health(
            total_bytes=usage.total, used_bytes=usage.used, free_bytes=usage.free,
            growth_bytes_per_day=growth, minimum_free_bytes=self.minimum_free_bytes,
            warning_usage_percent=float(os.getenv("FORWARD_DISK_WARNING_PERCENT", "75")),
            critical_usage_percent=float(os.getenv("FORWARD_DISK_CRITICAL_PERCENT", "90")),
            warning_free_gb=float(os.getenv("FORWARD_DISK_WARNING_FREE_GB", "100")),
            critical_free_gb=float(os.getenv("FORWARD_DISK_CRITICAL_FREE_GB", "10")),
            warning_days=float(os.getenv("FORWARD_DISK_WARNING_DAYS", "7")),
            critical_days=float(os.getenv("FORWARD_DISK_CRITICAL_DAYS", "3")),
        )
        return {
            "schema": PARTITION_SCHEMA, "root": str(self.root), "session_id": self.session_id,
            "events": self.event_count, "duplicates": self.duplicate_count,
            "bytes_written": self.bytes_written, "last_write_ts_ms": self.last_write_ts_ms,
            "active_segments": len(self._segments),
            "recovered_segments": self.recovered_segments,
            "oldest_active_receive_ts_ms": min(
                (item.first_receive_ts_ms for item in self._segments.values()
                 if item.first_receive_ts_ms is not None), default=None,
            ),
            "segment_seconds": self.max_segment_ms // 1_000,
            "minimum_free_bytes": self.minimum_free_bytes,
            "write_latency_ms": {"p50": percentile(.50), "p95": percentile(.95), "p99": percentile(.99)},
            **capacity,
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
