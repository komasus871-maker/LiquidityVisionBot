"""Run the preregistered second and final Stage B Development cycle."""
from __future__ import annotations

import json
from pathlib import Path

from services.stage_b_cycle2_lab import (
    CYCLE2_PREREGISTRATION_HASH, ROOT, StageBCycle2Lab, preregistration_payload, write_artifacts,
)


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    prereg_path = ROOT / f"preregistration-{CYCLE2_PREREGISTRATION_HASH}.json"
    if not prereg_path.exists():
        prereg_path.write_text(json.dumps(preregistration_payload(), indent=2, sort_keys=True), encoding="utf-8")
    materializations = sorted(Path("research_artifacts/stage_b_cycle1").glob("materialization-*.json"))
    if len(materializations) != 1:
        raise RuntimeError("expected exactly one immutable Stage B materialization artifact")
    materialization = json.loads(materializations[0].read_text(encoding="utf-8"))
    payload = StageBCycle2Lab(materialization).run_development()
    artifact, registry = write_artifacts(payload)
    print(json.dumps({"artifact": str(artifact), "registry": str(registry),
                      "preregistration_hash": CYCLE2_PREREGISTRATION_HASH,
                      "status": payload["status"], "selected_validation_candidate": payload["selected_validation_candidate"],
                      "candidate_summary": [{"id": item["candidate"]["id"], "signals": item["counts"]["family_signals"],
                                             "fills": item["metrics"]["trade_count"],
                                             "expectancy_r": item["metrics"]["expectancy_r"],
                                             "profit_factor": item["metrics"]["profit_factor"],
                                             "passed": item["development_gate"]["passed"],
                                             "failures": item["development_gate"]["failures"]}
                                            for item in payload["candidates"]]}, indent=2))


if __name__ == "__main__":
    main()
