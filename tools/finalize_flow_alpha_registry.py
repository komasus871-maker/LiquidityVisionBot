from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from services.flow_alpha_lab import MATERIALIZATION_ID, frozen_candidates
from services.flow_relative_value_lab import frozen_relative_value_candidates


ROOT = Path("research_artifacts/flow_alpha")
MATERIALIZATION = ROOT / f"materialization-{MATERIALIZATION_ID}.json"
DIRECTIONAL = ROOT / "directional-development-6de042680ab0dd95.json"
RELATIVE_VALUE = ROOT / "relative-value-development-dda42d5a4ba07f6b.json"
RELATIVE_PREREGISTRATION = ROOT / "relative_value_preregistration.json"


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    errors: list[str] = []
    materialization = _read(MATERIALIZATION)
    directional = _read(DIRECTIONAL)
    relative = _read(RELATIVE_VALUE)
    relative_preregistration = _read(RELATIVE_PREREGISTRATION)
    expected_hashes = {
        symbol: receipt["sha256"]
        for symbol, receipt in materialization["splits"]["FLOW_DEV"].items()
    }
    for path, record in ((DIRECTIONAL, directional), (RELATIVE_VALUE, relative)):
        for flag in ("validation_accessed", "blind_accessed", "derivatives_blind_accessed", "production_defaults_changed"):
            if record.get(flag) is not False:
                errors.append(f"{path.name}:{flag}")
        if record.get("split") != "FLOW_DEV" or record.get("materialization_id") != MATERIALIZATION_ID:
            errors.append(f"{path.name}:identity")
        if record.get("development_qualified_count") != 0 or record.get("frozen_validation_candidate") is not None:
            errors.append(f"{path.name}:unexpected_qualifier")
        for experiment in record.get("experiments", []):
            if experiment.get("dataset_hashes") != dict(sorted(expected_hashes.items())):
                errors.append(f"{path.name}:{experiment.get('experiment_id')}:dataset_hashes")
            if experiment.get("result") != "REJECTED_DEVELOPMENT":
                errors.append(f"{path.name}:{experiment.get('experiment_id')}:result")
            if set(experiment.get("costs", {})) != {"BASE", "HIGH", "STRESS"}:
                errors.append(f"{path.name}:{experiment.get('experiment_id')}:costs")
    directional_ids = {x.candidate_id for x in frozen_candidates()}
    artifact_directional_ids = {x["candidate"]["candidate_id"] for x in directional["experiments"]}
    if directional_ids != artifact_directional_ids or len(artifact_directional_ids) != 12:
        errors.append("directional_candidate_roster")
    relative_ids = {x.candidate_id for x in frozen_relative_value_candidates()}
    artifact_relative_ids = {x["candidate"]["candidate_id"] for x in relative["experiments"]}
    preregistered_relative_ids = {x["candidate_id"] for x in relative_preregistration["candidates"]}
    if relative_ids != artifact_relative_ids or relative_ids != preregistered_relative_ids or len(relative_ids) != 9:
        errors.append("relative_value_candidate_roster")
    for symbol, expected in expected_hashes.items():
        dataset = Path(materialization["splits"]["FLOW_DEV"][symbol]["path"])
        if _sha(dataset) != expected:
            errors.append(f"FLOW_DEV:{symbol}:content_hash")
    experiments = []
    for cycle, record in (("A_DIRECTIONAL", directional), ("B_CROSS_SECTIONAL", relative)):
        for experiment in record["experiments"]:
            experiments.append({"cycle": cycle, **experiment})
    registry = {
        "schema": "flow-alpha-experiment-registry-v1",
        "status": "NO_VERIFIED_FLOW_EDGE",
        "materialization_id": MATERIALIZATION_ID,
        "source_venue": "BINANCE_UM",
        "dataset_hashes": dict(sorted(expected_hashes.items())),
        "artifact_hashes": {
            MATERIALIZATION.name: _sha(MATERIALIZATION),
            DIRECTIONAL.name: _sha(DIRECTIONAL),
            RELATIVE_PREREGISTRATION.name: _sha(RELATIVE_PREREGISTRATION),
            RELATIVE_VALUE.name: _sha(RELATIVE_VALUE),
        },
        "candidate_count": len(experiments),
        "directional_candidate_count": len(directional["experiments"]),
        "relative_value_candidate_count": len(relative["experiments"]),
        "qualified_count": 0,
        "validation_accessed": False,
        "blind_accessed": False,
        "legacy_seen_test_accessed": False,
        "production_defaults_changed": False,
        "experiments": experiments,
    }
    payload = json.dumps(registry, indent=2, sort_keys=True, default=str) + "\n"
    registry_path = ROOT / "experiment-registry.json"
    registry_path.write_text(payload, encoding="utf-8")
    reconciliation = {
        "schema": "flow-alpha-reconciliation-v1",
        "valid": not errors,
        "errors": errors,
        "registry": registry_path.as_posix(),
        "registry_sha256": _sha(registry_path),
        "materialization_manifest_sha256": _sha(MATERIALIZATION),
        "candidate_count": len(experiments),
        "qualified_count": 0,
        "validation_accessed": False,
        "blind_accessed": False,
        "legacy_seen_test_accessed": False,
        "production_defaults_changed": False,
    }
    reconciliation_payload = json.dumps(reconciliation, indent=2, sort_keys=True) + "\n"
    identity = hashlib.sha256(reconciliation_payload.encode()).hexdigest()[:16]
    reconciliation_path = ROOT / f"reconciliation-{identity}.json"
    reconciliation_path.write_text(reconciliation_payload, encoding="utf-8")
    print(registry_path.as_posix())
    print(reconciliation_path.as_posix())
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
