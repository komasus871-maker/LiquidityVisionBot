from __future__ import annotations

import argparse
import json
from pathlib import Path

from services.forward_event_store import AppendOnlyEventStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only forward collector data-quality status")
    parser.add_argument("--database", default="data/forward_microstructure/forward-events.sqlite3")
    args = parser.parse_args()
    database = Path(args.database)
    if not database.is_file():
        raise SystemExit(f"forward dataset does not exist: {database}")
    store = AppendOnlyEventStore(database)
    print(json.dumps({
        "counts": store.counts(), "quality": store.quality_summary(),
        "dashboard": store.data_quality_dashboard(),
        "execution_authority": False,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
