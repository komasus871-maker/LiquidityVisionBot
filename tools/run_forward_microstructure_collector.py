from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from database.database import acquire_lease, release_lease
from database.schema_management import initialize_service_database
from services.forward_event_store import AppendOnlyEventStore
from services.forward_evidence_archive import ForwardEvidenceArchive
from services.forward_object_storage import S3CompatibleObjectStorage
from services.forward_public_collectors import (
    BinancePublicConnector, BingXPublicConnector, ForwardCollectorSupervisor,
    OKXPublicConnector,
)
from services.forward_runtime_state import ForwardRuntimeStateRepository, utc_now
from services.runtime_supervision import (
    PeriodicHeartbeatThread, bounded_thread_call, wait_for_authoritative_lease,
)


CONNECTORS = {
    "BINANCE": BinancePublicConnector,
    "OKX": OKXPublicConnector,
    "BINGX": BingXPublicConnector,
}
LEASE_NAME = "forward-microstructure-production-v1"


class LeaseLostError(RuntimeError):
    pass


class _SingleFlightThreadCall:
    """Bound a synchronous archive call without starting an overlapping retry."""

    def __init__(self) -> None:
        self._task: asyncio.Task[Any] | None = None

    @property
    def busy(self) -> bool:
        return self._task is not None and not self._task.done()

    async def run(
        self, function: Any, *args: Any, timeout_seconds: float, **kwargs: Any,
    ) -> Any:
        if self._task is not None:
            if not self._task.done():
                raise asyncio.TimeoutError("previous archive operation is still running")
            completed = self._task
            self._task = None
            completed.result()
        task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
        self._task = task
        try:
            result = await asyncio.wait_for(
                asyncio.shield(task), timeout=max(0.01, timeout_seconds),
            )
        except asyncio.TimeoutError:
            raise
        else:
            self._task = None
            return result


def _storage_root() -> Path:
    return Path(os.getenv("FORWARD_STORAGE_ROOT", "data/forward_microstructure"))


def _enabled(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def parser() -> argparse.ArgumentParser:
    root = _storage_root()
    result = argparse.ArgumentParser(description="Public-data-only forward microstructure collector")
    result.add_argument("--venues", default=os.getenv("FORWARD_VENUES", "BINANCE,OKX,BINGX"))
    result.add_argument("--symbols", default=os.getenv("FORWARD_SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT"))
    result.add_argument("--database", default=os.getenv(
        "FORWARD_METADATA_DB", str(root / "forward-metadata.sqlite3")))
    result.add_argument("--raw-partitions", default=os.getenv(
        "FORWARD_RAW_PARTITION_ROOT", str(root / "raw")))
    result.add_argument("--duration-seconds", type=float, default=0,
                        help="0 runs until interrupted; positive values are useful for certification")
    result.add_argument("--feature-interval-ms", type=int,
                        default=int(os.getenv("FORWARD_FEATURE_INTERVAL_MS", "1000")))
    result.add_argument("--minimum-free-bytes", type=int,
                        default=int(os.getenv("FORWARD_MIN_FREE_BYTES", str(10 * 1024**3))))
    result.add_argument("--print-capabilities", action="store_true")
    return result


def _iso_from_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()


def _parse_boundary(value: str) -> int | None:
    if not value.strip():
        return None
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("FORWARD_PREVIOUS_EVIDENCE_END_UTC must include a timezone")
    return int(parsed.timestamp() * 1000)


def _record_startup_gaps(
    store: AppendOnlyEventStore, shared: ForwardRuntimeStateRepository,
    venues: tuple[str, ...], symbols: tuple[str, ...], now_ms: int,
) -> str | None:
    previous_boundary = _parse_boundary(os.getenv("FORWARD_PREVIOUS_EVIDENCE_END_UTC", ""))
    migration_boundary: str | None = None
    for venue in venues:
        for symbol in symbols:
            checkpoint = store.latest_checkpoint(venue, symbol)
            start_ms = int(checkpoint["recorded_ts_ms"]) if checkpoint else previous_boundary
            if start_ms is None or start_ms >= now_ms:
                continue
            reason = "PROCESS_RESTART_GAP" if checkpoint else "LOCAL_TO_RENDER_MIGRATION_GAP"
            details = {
                "invented_events": False,
                "feature_eligible": False,
                "previous_checkpoint_state": checkpoint.get("state") if checkpoint else None,
            }
            store.append_gap(
                venue=venue, symbol=symbol, feed="ALL", start_ts_ms=start_ms,
                end_ts_ms=now_ms, reason=reason, severity="HIGH",
                replay_usable=False, details=details,
            )
            shared.record_gap(
                venue=venue, symbol=symbol, gap_start_at=_iso_from_ms(start_ms),
                gap_end_at=_iso_from_ms(now_ms), reason=reason, details=details,
            )
            migration_boundary = _iso_from_ms(start_ms)
    return migration_boundary


async def _run_archive_maintenance_cycle(
    archive: ForwardEvidenceArchive, store: AppendOnlyEventStore,
    storage_runtime: dict[str, Any], *, last_compaction: float,
    last_remote_audit: float, call_runner: _SingleFlightThreadCall | None = None,
) -> tuple[float, float, bool]:
    """Run one bounded archive cycle without allowing storage failure to stop collection."""
    timeout = max(
        0.01, float(os.getenv("FORWARD_ARCHIVE_OPERATION_TIMEOUT_SECONDS", "120")),
    )
    runner = call_runner or _SingleFlightThreadCall()
    storage_runtime["task_state"] = "RUNNING"
    try:
        store.raw_ledger.seal_completed()
        await runner.run(archive.process_pending, timeout_seconds=timeout)
        await runner.run(
            archive.evict_verified, force_to_cache_limit=True, timeout_seconds=timeout,
        )
        if time.monotonic() - last_remote_audit >= int(
            os.getenv("FORWARD_REMOTE_AUDIT_INTERVAL_SECONDS", "3600")
        ):
            await runner.run(archive.audit_remote, limit=20, timeout_seconds=timeout)
            last_remote_audit = time.monotonic()
        archive_health = archive.health()
        now_monotonic = time.monotonic()
        if (
            archive_health["pending_partitions"] == 0
            and archive_health["checksum_failures"] == 0
            and archive_health["last_successful_upload_at"]
            and now_monotonic - last_compaction >= int(
                os.getenv("FORWARD_METADATA_COMPACT_INTERVAL_SECONDS", "3600")
            )
        ):
            retain_ms = int(os.getenv("FORWARD_METADATA_RETENTION_SECONDS", "7200")) * 1_000
            await runner.run(
                store.compact_rebuildable_metadata,
                retain_after_ts_ms=time.time_ns() // 1_000_000 - retain_ms,
                remote_verified_through_ts_ms=int(
                    archive_health["newest_evidence_ts_ms"] or 0
                ),
                timeout_seconds=timeout,
            )
            last_compaction = now_monotonic
        storage_runtime.update({
            "task_state": "RUNNING", "last_success_at": utc_now(), "last_error": None,
            "health": archive_health,
        })
        return last_compaction, last_remote_audit, True
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        storage_runtime["task_state"] = "DEGRADED"
        storage_runtime["restart_count"] += 1
        storage_runtime["last_error"] = f"{type(exc).__name__}: {exc}"[:1000]
        logging.exception("Forward archive maintenance failed")
        return last_compaction, last_remote_audit, False


async def collect(args: argparse.Namespace) -> dict[str, Any]:
    venues = tuple(dict.fromkeys(value.strip().upper() for value in args.venues.split(",") if value.strip()))
    symbols = tuple(dict.fromkeys(value.strip().upper().replace("-", "") for value in args.symbols.split(",") if value.strip()))
    unknown = set(venues) - set(CONNECTORS)
    if unknown:
        raise ValueError(f"unsupported venues: {sorted(unknown)}")
    if not symbols or any(not symbol.endswith("USDT") for symbol in symbols):
        raise ValueError("symbols must be nonempty USDT perpetual identifiers")
    connectors = [CONNECTORS[venue](symbols) for venue in venues]
    if args.print_capabilities:
        return {
            "mode": "CONFIGURATION_ONLY", "venues": {item.venue.value: item.capabilities for item in connectors},
            "symbols": symbols, "execution_authority": False,
        }

    if os.getenv("FORWARD_COLLECTION_ENABLED", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        raise RuntimeError("FORWARD_COLLECTION_ENABLED must be explicitly true")

    initialize_service_database(service_name="forward-worker")
    shared = ForwardRuntimeStateRepository()
    identity_hash = shared.register_identity()
    process_identity = os.getenv("RENDER_INSTANCE_ID") or os.getenv("RENDER_SERVICE_ID") or "local"
    instance_id = f"{process_identity}:{socket.gethostname()}:{os.getpid()}"
    lease_seconds = max(60, int(os.getenv("FORWARD_LEASE_SECONDS", "120")))
    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(signum, shutdown_event.set)
        except (NotImplementedError, RuntimeError):
            pass
    acquired = await wait_for_authoritative_lease(
        acquire_lease, LEASE_NAME, instance_id, lease_seconds, shutdown=shutdown_event,
        base_backoff_seconds=float(os.getenv("FORWARD_LEASE_WAIT_BASE_SECONDS", "1")),
        max_backoff_seconds=float(os.getenv("FORWARD_LEASE_WAIT_MAX_SECONDS", "30")),
    )
    if not acquired:
        return {"status": "stopped_before_lease", "execution_authority": False}

    started_at = utc_now()
    store: AppendOnlyEventStore | None = None
    try:
        store = AppendOnlyEventStore(
            Path(args.database), raw_partition_root=Path(args.raw_partitions),
            minimum_free_bytes=args.minimum_free_bytes,
            program_identity=f"forward-microstructure-alpha-v1:{identity_hash}",
            max_segment_seconds=int(os.getenv("FORWARD_PARTITION_SECONDS", "300")),
        )
        archive: ForwardEvidenceArchive | None = None
        if _enabled("FORWARD_OBJECT_STORAGE_ENABLED"):
            archive = ForwardEvidenceArchive(
                Path(args.raw_partitions), S3CompatibleObjectStorage.from_environment(),
                prefix=os.getenv("FORWARD_OBJECT_PREFIX", "forward-evidence/schema-v2"),
                local_cache_bytes=int(os.getenv("FORWARD_LOCAL_CACHE_BYTES", str(5 * 1024**3))),
                minimum_free_bytes=args.minimum_free_bytes,
                registry_sink=shared.register_partition,
            )
        migration_boundary = _record_startup_gaps(
            store, shared, venues, symbols, time.time_ns() // 1_000_000,
        )
        supervisor = ForwardCollectorSupervisor(
            store=store, connectors=connectors, feature_interval_ms=args.feature_interval_ms,
            snapshot_sink=shared.publish_snapshot,
        )
    except BaseException:
        if store is not None:
            store.close()
        release_lease(LEASE_NAME, instance_id)
        raise

    collector_runtime: dict[str, Any] = {
        "task_state": "STARTING", "restart_count": 0,
        "last_restart_reason": None, "last_progress_at": utc_now(),
    }
    storage_runtime: dict[str, Any] = {
        "task_state": "DISABLED" if archive is None else "STARTING",
        "restart_count": 0, "last_error": None, "last_success_at": None,
    }
    archive_calls = _SingleFlightThreadCall()

    async def archive_maintenance() -> None:
        last_compaction = 0.0
        last_remote_audit = 0.0
        interval = max(15, int(os.getenv("FORWARD_ARCHIVE_INTERVAL_SECONDS", "30")))
        while not shutdown_event.is_set() and archive and store.raw_ledger:
            last_compaction, last_remote_audit, _ = await _run_archive_maintenance_cycle(
                archive, store, storage_runtime, last_compaction=last_compaction,
                last_remote_audit=last_remote_audit, call_runner=archive_calls,
            )
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    heartbeat_failed = asyncio.Event()
    heartbeat_failure: list[BaseException] = []

    def heartbeat_fatal(exc: BaseException) -> None:
        heartbeat_failure.append(exc)
        loop.call_soon_threadsafe(heartbeat_failed.set)

    def heartbeat_tick() -> None:
        if not acquire_lease(LEASE_NAME, instance_id, lease_seconds):
            raise LeaseLostError("authoritative forward collector lease was lost")
        health = supervisor.health()
        raw_health = store.raw_ledger.health() if store.raw_ledger else {}
        last_ms = raw_health.get("last_write_ts_ms")
        healthy = any(
            value.get("state") == "HEALTHY" for value in health["venues"].values()
        )
        publication_error = heartbeat_thread.last_error
        shared.heartbeat(
            instance_id=instance_id,
            state="RUNNING" if healthy and not publication_error else "DEGRADED",
            started_at=started_at,
            candidate_identity_hash=identity_hash, venues=health["venues"],
            storage=raw_health | (storage_runtime.get("health") or {}) | {
                    "metadata_database": str(Path(args.database)),
                    "metadata_database_bytes": (
                        Path(args.database).stat().st_size if Path(args.database).is_file() else 0
                    ),
                    "supervisor": dict(collector_runtime),
                    "archive_task": {
                        key: value for key, value in storage_runtime.items() if key != "health"
                    },
                },
            last_event_at=_iso_from_ms(last_ms) if last_ms else None,
            migration_boundary_at=migration_boundary,
            last_error=publication_error,
        )

    heartbeat_thread = PeriodicHeartbeatThread(
        heartbeat_tick,
        interval_seconds=max(15, min(30, int(os.getenv("FORWARD_HEARTBEAT_SECONDS", "20")))),
        fatal_exceptions=(LeaseLostError,), on_fatal=heartbeat_fatal,
        name="forward-heartbeat",
    )

    async def collector_loop() -> dict[str, Any]:
        backoff = 1.0
        while not shutdown_event.is_set():
            collector_runtime["task_state"] = "RUNNING"
            collector_runtime["last_progress_at"] = utc_now()
            try:
                result = await supervisor.run(duration_seconds=max(0, args.duration_seconds))
                if args.duration_seconds > 0:
                    return result
                reason = "COLLECTOR_TASK_EXITED"
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                reason = f"{type(exc).__name__}: {exc}"[:1000]
            collector_runtime["task_state"] = "RESTART_BACKOFF"
            collector_runtime["restart_count"] += 1
            collector_runtime["last_restart_reason"] = reason
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, 30)
        return supervisor.health()

    collector_task = asyncio.create_task(collector_loop(), name="forward-collector-supervisor")
    archive_task = asyncio.create_task(archive_maintenance(), name="forward-archive-maintenance")
    shutdown_task = asyncio.create_task(shutdown_event.wait(), name="forward-shutdown")
    heartbeat_failure_task = asyncio.create_task(
        heartbeat_failed.wait(), name="forward-heartbeat-failed",
    )
    try:
        heartbeat_thread.start()
        done, _ = await asyncio.wait(
            {collector_task, heartbeat_failure_task, shutdown_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if shutdown_task in done and shutdown_event.is_set():
            collector_task.cancel()
            await asyncio.gather(collector_task, return_exceptions=True)
            await bounded_thread_call(
                shared.heartbeat,
                instance_id=instance_id, state="STOPPING", started_at=started_at,
                candidate_identity_hash=identity_hash, venues=supervisor.health()["venues"],
                storage=store.raw_ledger.health() if store.raw_ledger else {},
                migration_boundary_at=migration_boundary,
                timeout_seconds=20,
            )
            return supervisor.health()
        if heartbeat_failure_task in done and heartbeat_failed.is_set():
            raise heartbeat_failure[-1] if heartbeat_failure else RuntimeError(
                "forward heartbeat thread exited"
            )
        return await collector_task
    except BaseException as exc:
        try:
            await bounded_thread_call(
                shared.heartbeat,
                instance_id=instance_id, state="FAILED", started_at=started_at,
                candidate_identity_hash=identity_hash, venues=supervisor.health()["venues"],
                storage=store.raw_ledger.health() if store.raw_ledger else {},
                migration_boundary_at=migration_boundary, last_error=str(exc)[:1000],
                timeout_seconds=20,
            )
        except Exception:
            pass
        raise
    finally:
        shutdown_event.set()
        heartbeat_thread.stop(timeout_seconds=20)
        for task in (collector_task, heartbeat_failure_task, archive_task, shutdown_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(
            collector_task, heartbeat_failure_task, archive_task, shutdown_task,
            return_exceptions=True,
        )
        store.close()
        if archive and not archive_calls.busy:
            try:
                await archive_calls.run(
                    archive.process_pending, force=True, timeout_seconds=120,
                )
                await archive_calls.run(
                    archive.evict_verified, force_to_cache_limit=True, timeout_seconds=120,
                )
            except Exception:
                # Sealed local partitions remain on disk for restart recovery.
                pass
        release_lease(LEASE_NAME, instance_id)


def main() -> int:
    args = parser().parse_args()
    result = asyncio.run(collect(args))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
