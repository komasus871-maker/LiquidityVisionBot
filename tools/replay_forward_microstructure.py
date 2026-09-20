from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from services.forward_event_replay import ForwardEventReplay


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministically replay a forward raw-event ledger")
    parser.add_argument("--source", default="data/forward_microstructure/forward-events.sqlite3")
    parser.add_argument("--output", required=True, help="must name a new SQLite database")
    parser.add_argument("--raw-partitions", default=None)
    parser.add_argument("--feature-interval-ms", type=int, default=1000)
    args = parser.parse_args()
    result = ForwardEventReplay(
        args.source, args.output, raw_partition_root=args.raw_partitions,
        feature_interval_ms=args.feature_interval_ms,
    ).run()
    print(json.dumps(asdict(result), indent=2, sort_keys=True))
    return 0 if result.decision_ids_match and result.feature_ids_match else 2


if __name__ == "__main__":
    raise SystemExit(main())
