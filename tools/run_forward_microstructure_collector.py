from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from database.database import acquire_lease, create_tables, release_lease
from services.forward_event_store import AppendOnlyEventStore
from services.forward_evidence_archive import ForwardEvidenceArchive
from services.forward_object_storage import S3CompatibleObjectStorage
from services.forward_public_collectors import (
    BinancePublicConnector, BingXPublicConnector, ForwardCollectorSupervisor,
    OKXPublicConnector,
)
from services.forward_runtime_state import ForwardRuntimeStateRepository, utc_now


CONNECTORS = {
    "BINANCE": BinancePublicConnector,
    "OKX": OKXPublicConnector,
    "BINGX": BingXPublicConnector,
}
LEASE_NAME = "forward-microstructure-production-v1"


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

    create_tables()
    shared = ForwardRuntimeStateRepository()
    identity_hash = shared.register_identity()
    instance_id = (
        os.getenv("RENDER_INSTANCE_ID") or os.getenv("RENDER_SERVICE_ID") or
        f"{socket.gethostname()}-{os.getpid()}"
    )
    lease_seconds = max(60, int(os.getenv("FORWARD_LEASE_SECONDS", "120")))
    if not acquire_lease(LEASE_NAME, instance_id, lease_seconds):
        raise RuntimeError("authoritative forward collector lease is already held")

    started_at = utc_now()
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

    async def heartbeat() -> None:
        last_compaction = 0.0
        last_remote_audit = 0.0
        while True:
            if not acquire_lease(LEASE_NAME, instance_id, lease_seconds):
                raise RuntimeError("authoritative forward collector lease was lost")
            health = supervisor.health()
            raw_health = store.raw_ledger.health() if store.raw_ledger else {}
            archive_health: dict[str, Any] = {}
            if archive and store.raw_ledger:
                store.raw_ledger.seal_completed()
                await asyncio.to_thread(archive.process_pending)
                await asyncio.to_thread(archive.evict_verified, force_to_cache_limit=True)
                if time.monotonic() - last_remote_audit >= int(
                    os.getenv("FORWARD_REMOTE_AUDIT_INTERVAL_SECONDS", "3600")
                ):
                    await asyncio.to_thread(archive.audit_remote, limit=20)
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
                    await asyncio.to_thread(
                        store.compact_rebuildable_metadata,
                        retain_after_ts_ms=time.time_ns() // 1_000_000 - retain_ms,
                        remote_verified_through_ts_ms=int(
                            archive_health["newest_evidence_ts_ms"] or 0
                        ),
                    )
                    last_compaction = now_monotonic
            last_ms = raw_health.get("last_write_ts_ms")
            shared.heartbeat(
                instance_id=instance_id, state="RUNNING", started_at=started_at,
                candidate_identity_hash=identity_hash, venues=health["venues"],
                storage=raw_health | archive_health | {
                    "metadata_database": str(Path(args.database)),
                    "metadata_database_bytes": (
                        Path(args.database).stat().st_size if Path(args.database).is_file() else 0
                    ),
                },
                last_event_at=_iso_from_ms(last_ms) if last_ms else None,
                migration_boundary_at=migration_boundary,
            )
            await asyncio.sleep(max(10, lease_seconds // 3))

    collector_task = asyncio.create_task(
        supervisor.run(duration_seconds=max(0, args.duration_seconds)), name="forward-collector"
    )
    heartbeat_task = asyncio.create_task(heartbeat(), name="forward-heartbeat")
    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(signum, shutdown_event.set)
        except (NotImplementedError, RuntimeError):
            pass
    shutdown_task = asyncio.create_task(shutdown_event.wait(), name="forward-shutdown")
    try:
        done, _ = await asyncio.wait(
            {collector_task, heartbeat_task, shutdown_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if shutdown_task in done and shutdown_event.is_set():
            collector_task.cancel()
            await asyncio.gather(collector_task, return_exceptions=True)
            shared.heartbeat(
                instance_id=instance_id, state="STOPPING", started_at=started_at,
                candidate_identity_hash=identity_hash, venues=supervisor.health()["venues"],
                storage=store.raw_ledger.health() if store.raw_ledger else {},
                migration_boundary_at=migration_boundary,
            )
            return supervisor.health()
        if heartbeat_task in done:
            exception = heartbeat_task.exception()
            if exception:
                raise exception
        return await collector_task
    except BaseException as exc:
        try:
            shared.heartbeat(
                instance_id=instance_id, state="FAILED", started_at=started_at,
                candidate_identity_hash=identity_hash, venues=supervisor.health()["venues"],
                storage=store.raw_ledger.health() if store.raw_ledger else {},
                migration_boundary_at=migration_boundary, last_error=str(exc)[:1000],
            )
        except Exception:
            pass
        raise
    finally:
        for task in (collector_task, heartbeat_task, shutdown_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(collector_task, heartbeat_task, shutdown_task, return_exceptions=True)
        store.close()
        if archive:
            try:
                await asyncio.to_thread(archive.process_pending, force=True)
                await asyncio.to_thread(archive.evict_verified, force_to_cache_limit=True)
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
