from __future__ import annotations

import argparse
import json
from pathlib import Path
from dataclasses import asdict

from services.forward_event_replay import ForwardEventReplay


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministically replay a forward raw-event ledger")
    parser.add_argument("--source", default="data/forward_microstructure/forward-events.sqlite3")
    parser.add_argument("--output", required=True, help="must name a new SQLite database")
    parser.add_argument("--raw-partitions", default=None)
    parser.add_argument("--remote", action="store_true",
                        help="stream verified partitions from the configured S3-compatible archive")
    parser.add_argument("--cache-root", default=None,
                        help="bounded temporary cache used for remote replay buckets")
    parser.add_argument("--start-ts-ms", type=int, default=None)
    parser.add_argument("--end-ts-ms", type=int, default=None)
    parser.add_argument("--feature-interval-ms", type=int, default=1000)
    args = parser.parse_args()
    registry = backend = None
    if args.remote:
        from services.forward_object_storage import S3CompatibleObjectStorage
        from services.forward_runtime_state import ForwardRuntimeStateRepository
        registry = ForwardRuntimeStateRepository().replay_partitions(
            start_ts_ms=args.start_ts_ms, end_ts_ms=args.end_ts_ms,
        )
        backend = S3CompatibleObjectStorage.from_environment()
        if not registry:
            raise RuntimeError("no remotely verified partitions match the requested replay window")
    result = ForwardEventReplay(
        args.source, args.output, raw_partition_root=args.raw_partitions,
        feature_interval_ms=args.feature_interval_ms,
        remote_registry=registry, object_backend=backend,
        cache_root=args.cache_root or Path(args.output).parent / ".forward-replay-cache",
    ).run()
    print(json.dumps(asdict(result), indent=2, sort_keys=True))
    return 0 if result.decision_ids_match and result.feature_ids_match else 2


if __name__ == "__main__":
    raise SystemExit(main())
