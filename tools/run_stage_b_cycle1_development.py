"""Run the frozen Stage B Cycle 1 Development-only experiment."""
from __future__ import annotations

import json
from pathlib import Path

from services.stage_b_alpha_lab import StageBAlphaLab, write_development_artifact


def main() -> None:
    root = Path("research_artifacts/stage_b_cycle1")
    paths = sorted(root.glob("materialization-*.json"))
    if len(paths) != 1:
        raise RuntimeError(f"expected exactly one Stage B materialization artifact, found {len(paths)}")
    materialization = json.loads(paths[0].read_text(encoding="utf-8"))
    payload = StageBAlphaLab(materialization).run_development()
    path = write_development_artifact(payload)
    print(json.dumps({
        "artifact": str(path), "artifact_hash": payload["artifact_hash"],
        "status": payload["status"], "selected_validation_candidate": payload["selected_validation_candidate"],
        "candidate_summary": [{"id": item["candidate"]["id"], "fills": item["metrics"]["trade_count"],
                               "expectancy_r": item["metrics"]["expectancy_r"],
                               "profit_factor": item["metrics"]["profit_factor"],
                               "passed": item["development_gate"]["passed"],
                               "failures": item["development_gate"]["failures"]}
                              for item in payload["candidates"]],
    }, indent=2))


if __name__ == "__main__":
    main()
