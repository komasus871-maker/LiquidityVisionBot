"""Seal/upload/verify/evict lifecycle for immutable forward evidence."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from services.forward_object_storage import (
    ForwardObjectStorage, ObjectIntegrityError, ObjectStorageError, sha256_file,
)
from services.forward_partition_store import GIB, StorageUnsafeError, read_segment


ARCHIVE_STATE_SCHEMA = "forward-remote-archive-state-v1"
DEFAULT_PREFIX = "forward-evidence/schema-v2"


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def object_key(manifest: dict[str, Any], prefix: str = DEFAULT_PREFIX) -> str:
    stamp = datetime.fromtimestamp(
        int(manifest["first_receive_ts_ms"]) / 1000, tz=timezone.utc,
    )
    return "/".join((
        prefix.strip("/"), str(manifest["venue"]), str(manifest["symbol"]),
        f"{stamp.year:04d}", f"{stamp.month:02d}", f"{stamp.day:02d}",
        f"{stamp.hour:02d}", f"{manifest['partition_id']}.fwdz",
    ))


@dataclass(frozen=True)
class ArchiveResult:
    manifest_path: Path
    object_key: str
    state: str
    byte_count: int
    upload_latency_ms: float | None = None
    error: str | None = None


class ForwardEvidenceArchive:
    """Durable local state machine around a provider-neutral object backend."""

    def __init__(
        self, root: str | Path, backend: ForwardObjectStorage, *,
        prefix: str = DEFAULT_PREFIX, local_cache_bytes: int = 5 * GIB,
        minimum_free_bytes: int = 5 * GIB,
        registry_sink: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.backend = backend
        self.prefix = prefix.strip("/")
        self.local_cache_bytes = max(0, int(local_cache_bytes))
        self.minimum_free_bytes = max(0, int(minimum_free_bytes))
        self.registry_sink = registry_sink
        self.last_success_at: str | None = None
        self.last_upload_latency_ms: float | None = None
        self.checksum_failures = 0
        self.failed_uploads = 0
        self.manifest_inconsistencies = 0
        self.missing_remote_objects = 0
        self.last_error: str | None = None

    @staticmethod
    def _state_path(manifest_path: Path) -> Path:
        return manifest_path.with_suffix(".remote.json")

    @staticmethod
    def _load_manifest(path: Path) -> dict[str, Any]:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        required = {
            "schema", "partition_id", "relative_path", "sha256", "event_count",
            "byte_count", "first_receive_ts_ms", "last_receive_ts_ms", "venue", "symbol",
        }
        missing = sorted(required - set(manifest))
        if missing:
            raise StorageUnsafeError(f"manifest {path} is missing: {', '.join(missing)}")
        return manifest

    def manifests(self) -> list[Path]:
        return sorted(self.root.rglob("*.manifest.json"))

    def _publish(self, manifest: dict[str, Any], state: dict[str, Any]) -> None:
        if self.registry_sink is None:
            return
        try:
            self.registry_sink(manifest | state)
        except Exception as exc:
            # Local manifests are the recovery authority when PostgreSQL is unavailable.
            self.last_error = f"partition registry unavailable: {exc}"

    def process_manifest(self, manifest_path: str | Path) -> ArchiveResult:
        path = Path(manifest_path)
        try:
            manifest = self._load_manifest(path)
            source = self.root / str(manifest["relative_path"])
            key = object_key(manifest, self.prefix)
            if not source.is_file():
                state_path = self._state_path(path)
                state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
                if state.get("state") == "REMOTE_VERIFIED" and state.get("object_key") == key:
                    head = self.backend.verify_partition(
                        key, size=int(manifest["byte_count"]),
                        checksum_sha256=str(manifest["sha256"]),
                    )
                    self.backend.verify_partition(
                        str(state["manifest_object_key"]), size=path.stat().st_size,
                        checksum_sha256=str(state["manifest_sha256"]),
                    )
                    return ArchiveResult(path, key, "REMOTE_VERIFIED", head.size)
                raise StorageUnsafeError(f"sealed local partition is missing before verification: {source}")
            if source.stat().st_size != int(manifest["byte_count"]):
                raise ObjectIntegrityError(f"local partition size differs from manifest: {source}")
            if sha256_file(source) != str(manifest["sha256"]):
                raise ObjectIntegrityError(f"local partition checksum differs from manifest: {source}")

            previous_path = self._state_path(path)
            previous = json.loads(previous_path.read_text(encoding="utf-8")) if previous_path.is_file() else {}
            attempts = int(previous.get("attempts") or 0) + 1
            started = time.perf_counter_ns()
            metadata = {
                "sha256": str(manifest["sha256"]),
                "partition-id": str(manifest["partition_id"]),
                "schema": str(manifest["schema"]),
                "event-count": str(manifest["event_count"]),
                "program-identity": str(manifest.get("program_identity") or ""),
            }
            head = self.backend.put_partition(key, source, metadata)
            head = self.backend.verify_partition(
                key, size=int(manifest["byte_count"]),
                checksum_sha256=str(manifest["sha256"]),
            )
            manifest_checksum = sha256_file(path)
            manifest_key = key + ".manifest.json"
            self.backend.put_partition(
                manifest_key, path, {
                    "sha256": manifest_checksum,
                    "partition-id": str(manifest["partition_id"]),
                    "schema": str(manifest["schema"]),
                    "artifact-kind": "manifest",
                },
            )
            self.backend.verify_partition(
                manifest_key, size=path.stat().st_size,
                checksum_sha256=manifest_checksum,
            )
            latency_ms = (time.perf_counter_ns() - started) / 1_000_000
            now = datetime.now(timezone.utc).isoformat()
            state = {
                "schema": ARCHIVE_STATE_SCHEMA, "state": "REMOTE_VERIFIED",
                "partition_id": manifest["partition_id"], "object_key": key,
                "manifest_object_key": manifest_key,
                "manifest_sha256": manifest_checksum,
                "sha256": manifest["sha256"], "byte_count": head.size,
                "attempts": attempts, "verified_at_utc": now,
                "upload_latency_ms": round(latency_ms, 3),
                "retention_state": previous.get("retention_state", "FROZEN_30_DAY"),
                "local_eviction_eligible": True, "last_error": None,
            }
            _atomic_json(previous_path, state)
            self.last_success_at = now
            self.last_upload_latency_ms = latency_ms
            self.last_error = None
            self._publish(manifest, state)
            return ArchiveResult(path, key, "REMOTE_VERIFIED", head.size, latency_ms)
        except ObjectIntegrityError as exc:
            self.checksum_failures += 1
            self.last_error = str(exc)
            self._write_failure(path, str(exc), integrity=True)
            raise
        except (ObjectStorageError, StorageUnsafeError, OSError, ValueError, json.JSONDecodeError) as exc:
            self.failed_uploads += 1
            self.last_error = str(exc)
            self._write_failure(path, str(exc), integrity=False)
            raise

    def _write_failure(self, manifest_path: Path, error: str, *, integrity: bool) -> None:
        state_path = self._state_path(manifest_path)
        previous: dict[str, Any] = {}
        if state_path.is_file():
            try:
                previous = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        attempts = int(previous.get("attempts") or 0) + 1
        backoff = min(3_600, 5 * (2 ** min(attempts - 1, 9)))
        state = {
            **previous, "schema": ARCHIVE_STATE_SCHEMA,
            "state": "INTEGRITY_INCIDENT" if integrity else "UPLOAD_PENDING",
            "attempts": attempts, "last_error": error[:1000],
            "last_attempt_at_utc": datetime.now(timezone.utc).isoformat(),
            "next_retry_epoch": time.time() + backoff,
            "local_eviction_eligible": False,
        }
        _atomic_json(state_path, state)
        try:
            manifest = self._load_manifest(manifest_path)
            key = object_key(manifest, self.prefix)
            self._publish(manifest, state | {
                "object_key": key,
                "manifest_object_key": key + ".manifest.json",
                "retention_state": state.get("retention_state", "FROZEN_30_DAY"),
            })
        except (OSError, ValueError, json.JSONDecodeError, StorageUnsafeError):
            self.manifest_inconsistencies += 1

    def process_pending(self, *, force: bool = False) -> list[ArchiveResult]:
        results: list[ArchiveResult] = []
        for manifest_path in self.manifests():
            state_path = self._state_path(manifest_path)
            state: dict[str, Any] = {}
            if state_path.is_file():
                try:
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    self.manifest_inconsistencies += 1
            if state.get("state") == "REMOTE_VERIFIED":
                continue
            if state.get("state") == "INTEGRITY_INCIDENT" and not force:
                continue
            if not force and float(state.get("next_retry_epoch") or 0) > time.time():
                continue
            try:
                results.append(self.process_manifest(manifest_path))
            except (ObjectStorageError, StorageUnsafeError):
                continue
        return results

    def evict_verified(self, *, force_to_cache_limit: bool = False) -> int:
        candidates: list[tuple[float, Path, dict[str, Any], dict[str, Any]]] = []
        local_bytes = 0
        for manifest_path in self.manifests():
            manifest = self._load_manifest(manifest_path)
            source = self.root / str(manifest["relative_path"])
            if not source.is_file():
                continue
            local_bytes += source.stat().st_size
            state_path = self._state_path(manifest_path)
            if not state_path.is_file():
                continue
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if state.get("state") == "REMOTE_VERIFIED" and state.get("sha256") == manifest["sha256"]:
                candidates.append((float(manifest["last_receive_ts_ms"]), source, manifest, state))
        if not force_to_cache_limit and local_bytes <= self.local_cache_bytes:
            return 0
        removed = 0
        target = self.local_cache_bytes
        for _, source, manifest, state in sorted(candidates):
            if local_bytes <= target:
                break
            # Re-HEAD immediately before eviction; stale sidecar state alone is insufficient.
            self.backend.verify_partition(
                str(state["object_key"]), size=int(manifest["byte_count"]),
                checksum_sha256=str(manifest["sha256"]),
            )
            self.backend.verify_partition(
                str(state["manifest_object_key"]), size=manifest_path.stat().st_size,
                checksum_sha256=str(state["manifest_sha256"]),
            )
            size = source.stat().st_size
            source.unlink()
            local_bytes -= size
            removed += size
            state["local_state"] = "EVICTED_REMOTE_VERIFIED"
            state["evicted_at_utc"] = datetime.now(timezone.utc).isoformat()
            _atomic_json(self._state_path(source.with_suffix(".manifest.json")), state)
        return removed

    def audit_remote(self, *, limit: int = 20) -> int:
        """Bounded audit of recently verified objects; never evicts on failure."""
        checked = 0
        for manifest_path in reversed(self.manifests()):
            if checked >= max(0, int(limit)):
                break
            state_path = self._state_path(manifest_path)
            if not state_path.is_file():
                continue
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if state.get("state") != "REMOTE_VERIFIED":
                continue
            manifest = self._load_manifest(manifest_path)
            checked += 1
            try:
                self.backend.verify_partition(
                    str(state["object_key"]), size=int(manifest["byte_count"]),
                    checksum_sha256=str(manifest["sha256"]),
                )
                self.backend.verify_partition(
                    str(state["manifest_object_key"]), size=manifest_path.stat().st_size,
                    checksum_sha256=str(state["manifest_sha256"]),
                )
            except ObjectIntegrityError as exc:
                self.checksum_failures += 1
                self.last_error = str(exc)
            except ObjectStorageError as exc:
                self.missing_remote_objects += 1
                self.last_error = str(exc)
        return checked

    def health(self, *, outage_growth_bytes_per_day: float = 13_840_635_579) -> dict[str, Any]:
        pending_bytes = verified_bytes = current_30_day_bytes = local_bytes = 0
        pending = verified = incidents = 0
        oldest: int | None = None
        newest: int | None = None
        for manifest_path in self.manifests():
            try:
                manifest = self._load_manifest(manifest_path)
            except (OSError, ValueError, json.JSONDecodeError, StorageUnsafeError):
                self.manifest_inconsistencies += 1
                continue
            first = int(manifest["first_receive_ts_ms"])
            last = int(manifest["last_receive_ts_ms"])
            oldest = first if oldest is None else min(oldest, first)
            newest = last if newest is None else max(newest, last)
            source = self.root / str(manifest["relative_path"])
            if source.is_file():
                local_bytes += source.stat().st_size
            state_path = self._state_path(manifest_path)
            state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
            if state.get("state") == "REMOTE_VERIFIED":
                verified += 1
                verified_bytes += int(manifest["byte_count"])
                if last >= time.time_ns() // 1_000_000 - 30 * 86_400_000:
                    current_30_day_bytes += int(manifest["byte_count"])
            else:
                pending += 1
                pending_bytes += int(manifest["byte_count"])
                incidents += int(state.get("state") == "INTEGRITY_INCIDENT")
        usage = shutil.disk_usage(self.root)
        usable = max(0, int(usage.free) - self.minimum_free_bytes)
        hours = usable / max(1.0, float(outage_growth_bytes_per_day)) * 24
        if incidents or self.checksum_failures:
            status = "CRITICAL"
        elif self.last_error or pending:
            status = "DEGRADED"
        else:
            status = "HEALTHY"
        return {
            "object_storage_status": status,
            "last_successful_upload_at": self.last_success_at,
            "last_upload_latency_ms": (
                round(self.last_upload_latency_ms, 2) if self.last_upload_latency_ms is not None else None
            ),
            "pending_partitions": pending, "pending_upload_bytes": pending_bytes,
            "failed_uploads": self.failed_uploads,
            "remotely_verified_partitions": verified,
            "remotely_verified_bytes": verified_bytes,
            "current_30_day_stored_bytes": current_30_day_bytes,
            "local_partition_bytes": local_bytes,
            "estimated_spool_hours_remaining": round(hours, 2),
            "oldest_evidence_ts_ms": oldest, "newest_evidence_ts_ms": newest,
            "checksum_failures": self.checksum_failures,
            "manifest_inconsistencies": self.manifest_inconsistencies,
            "missing_remote_objects": self.missing_remote_objects,
            "last_storage_error": self.last_error,
            "local_cache_limit_bytes": self.local_cache_bytes,
            "minimum_free_bytes": self.minimum_free_bytes,
        }

    def release_remote(self, manifest_path: str | Path, *, authorized: bool = False) -> None:
        """Delete only explicitly releasable evidence; never called by collection loops."""
        if not authorized:
            raise PermissionError("remote retention deletion requires explicit authorization")
        path = Path(manifest_path)
        state_path = self._state_path(path)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("state") != "REMOTE_VERIFIED" or state.get("retention_state") != "RELEASABLE":
            raise StorageUnsafeError("partition is protected by retention policy")
        self.backend.delete_partition(str(state["object_key"]))
        self.backend.delete_partition(str(state["manifest_object_key"]))
        state["state"] = "REMOTE_DELETED"
        state["deleted_at_utc"] = datetime.now(timezone.utc).isoformat()
        _atomic_json(state_path, state)


class RemotePartitionReplay:
    """Deterministic bucket-at-a-time replay with a bounded local cache."""

    def __init__(self, backend: ForwardObjectStorage, cache_root: str | Path):
        self.backend = backend
        self.cache_root = Path(cache_root)
        self.cache_root.mkdir(parents=True, exist_ok=True)

    def records(self, registry: Iterable[dict[str, Any]]) -> Iterator[dict[str, Any]]:
        buckets: dict[int, list[dict[str, Any]]] = {}
        for item in registry:
            buckets.setdefault(int(item["bucket_start_ts_ms"]), []).append(dict(item))
        for bucket in sorted(buckets):
            bucket_root = self.cache_root / str(bucket)
            downloaded: list[Path] = []
            try:
                for item in sorted(buckets[bucket], key=lambda row: str(row["partition_id"])):
                    target = bucket_root / f"{item['partition_id']}.fwdz"
                    self.backend.get_partition(str(item["object_key"]), target)
                    if target.stat().st_size != int(item["byte_count"]):
                        raise ObjectIntegrityError(f"downloaded size mismatch: {target}")
                    if sha256_file(target) != str(item["sha256"]):
                        raise ObjectIntegrityError(f"downloaded checksum mismatch: {target}")
                    downloaded.append(target)
                iterators = [iter(read_segment(path)) for path in downloaded]
                import heapq
                heap: list[tuple[int, int, dict[str, Any]]] = []
                for index, iterator in enumerate(iterators):
                    try:
                        record = next(iterator)
                    except StopIteration:
                        continue
                    heap.append((int(record["ingest_order_ns"]), index, record))
                heapq.heapify(heap)
                while heap:
                    _, index, record = heapq.heappop(heap)
                    yield record
                    try:
                        following = next(iterators[index])
                    except StopIteration:
                        continue
                    heapq.heappush(heap, (int(following["ingest_order_ns"]), index, following))
            finally:
                for target in downloaded:
                    target.unlink(missing_ok=True)
                try:
                    bucket_root.rmdir()
                except OSError:
                    pass


def reconstruct_registry_from_remote(
    backend: ForwardObjectStorage, cache_root: str | Path, *, prefix: str = DEFAULT_PREFIX,
) -> Iterator[dict[str, Any]]:
    """Rebuild replay identity from immutable remote manifests if PostgreSQL is lost."""
    root = Path(cache_root)
    root.mkdir(parents=True, exist_ok=True)
    for head in backend.list_partitions(prefix.strip("/")):
        if not head.key.endswith(".fwdz.manifest.json"):
            continue
        target = root / (hashlib.sha256(head.key.encode("utf-8")).hexdigest() + ".json")
        try:
            backend.get_partition(head.key, target)
            if target.stat().st_size != head.size or sha256_file(target) != head.checksum_sha256:
                raise ObjectIntegrityError(f"remote manifest checksum mismatch: {head.key}")
            manifest = json.loads(target.read_text(encoding="utf-8"))
            data_key = head.key.removesuffix(".manifest.json")
            backend.verify_partition(
                data_key, size=int(manifest["byte_count"]),
                checksum_sha256=str(manifest["sha256"]),
            )
            yield manifest | {
                "object_key": data_key, "manifest_object_key": head.key,
                "state": "REMOTE_VERIFIED", "replay_available": True,
            }
        finally:
            target.unlink(missing_ok=True)
