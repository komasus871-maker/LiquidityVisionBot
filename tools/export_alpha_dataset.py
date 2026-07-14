from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from database.database import connect, create_tables
from services.alpha_research import AlphaResearchEngine


def load_signals() -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM signals ORDER BY id ASC").fetchall()
    return [dict(row) for row in rows]


def main() -> int:
    parser = argparse.ArgumentParser(description="Export Liquidity Vision alpha-research dataset")
    parser.add_argument("--output", default="exports/alpha_dataset.csv")
    parser.add_argument("--format", choices=("csv", "jsonl"), default="csv")
    parser.add_argument("--include-rejected", action="store_true")
    args = parser.parse_args()

    create_tables()
    engine = AlphaResearchEngine()
    rows = engine.dataset(load_signals(), usable_only=not args.include_rejected)
    output = Path(args.output)
    if args.format == "jsonl":
        engine.export_jsonl(rows, output)
    else:
        engine.export_csv(rows, output)

    summary = {
        "output": str(output),
        "rows": len(rows),
        "overall": asdict(engine.metrics(rows)),
        "by_timeframe": {k: asdict(v) for k, v in engine.grouped_metrics(rows, "timeframe").items()},
        "by_regime": {k: asdict(v) for k, v in engine.grouped_metrics(rows, "regime").items()},
        "by_setup": {k: asdict(v) for k, v in engine.grouped_metrics(rows, "setup_key").items()},
    }
    report = output.with_suffix(output.suffix + ".report.json")
    report.write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
