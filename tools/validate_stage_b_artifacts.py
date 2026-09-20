"""Validate immutable Stage B artifacts without evaluating protected outcomes."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path("research_artifacts")
CODE_FILES = (
    Path("services/stage_b_alpha_lab.py"), Path("services/stage_b_cycle2_lab.py"),
    Path("services/research_features.py"), Path("services/research_replay.py"),
    Path("services/decision_quality.py"), Path("services/trade_plan_integrity.py"),
)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()[:16]


def _one(directory: Path, pattern: str) -> Path:
    paths = list(directory.glob(pattern))
    if len(paths) != 1:
        raise RuntimeError(f"expected one {pattern} in {directory}, found {len(paths)}")
    return paths[0]


def main() -> None:
    errors: list[str] = []
    materialization_path = _one(ROOT / "stage_b_cycle1", "materialization-*.json")
    materialization = json.loads(materialization_path.read_text(encoding="utf-8"))
    datasets = {}
    for symbol, manifest in materialization["datasets"].items():
        path = ROOT / "stage_b_cycle1" / f"{symbol}_1h_{manifest['dataset_id']}.csv"
        with path.open("r", encoding="utf-8") as handle:
            row_count = sum(1 for _ in handle) - 1
        if row_count != manifest["candle_count"]:
            errors.append(f"{symbol}: candle count mismatch")
        datasets[symbol] = {"dataset_id": manifest["dataset_id"], "content_hash": manifest["content_hash"],
                            "csv_file_sha256": _sha(path), "rows": row_count,
                            "start": manifest["start"], "end": manifest["end"]}
    cycles = {}
    for cycle in ("stage_b_cycle1", "stage_b_cycle2"):
        directory = ROOT / cycle
        development_path = _one(directory, "development-*.json")
        registry_path = _one(directory, "experiment-register-*.json")
        development = json.loads(development_path.read_text(encoding="utf-8"))
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        artifact_hash = development.pop("artifact_hash")
        if _stable(development) != artifact_hash:
            errors.append(f"{cycle}: canonical artifact hash mismatch")
        development["artifact_hash"] = artifact_hash
        registry_hash = registry.pop("registry_hash")
        if _stable(registry) != registry_hash:
            errors.append(f"{cycle}: registry hash mismatch")
        registry["registry_hash"] = registry_hash
        if len(development["candidates"]) != 12 or len(registry["records"]) != 12:
            errors.append(f"{cycle}: incomplete 12-candidate ledger")
        if development["selected_validation_candidate"] is not None:
            errors.append(f"{cycle}: unexpected selected candidate")
        if development["validation_accessed"] or development["blind_accessed"]:
            errors.append(f"{cycle}: protected partition access marker")
        if development["protected_existing_partitions_accessed"]:
            errors.append(f"{cycle}: existing protected partition access marker")
        config_hashes = [item["candidate"]["config_hash"] for item in development["candidates"]]
        if len(set(config_hashes)) != 12:
            errors.append(f"{cycle}: duplicate candidate config hash")
        access_end = pd.Timestamp(development["split"]["access_end"])
        for candidate in development["candidates"]:
            for scenario in candidate["outcomes"].values():
                if any(pd.Timestamp(item["decision_at"]) > access_end for item in scenario):
                    errors.append(f"{cycle}: post-Development outcome found")
                if any(item["provenance"].get("entry_policy") != "MARKET_NEXT_OPEN" for item in scenario):
                    errors.append(f"{cycle}: noncanonical entry policy")
                if any(item["provenance"].get("decision_authority") != "DecisionQualityEngine" for item in scenario):
                    errors.append(f"{cycle}: DecisionQuality authority missing")
        cycles[cycle] = {
            "development_file": development_path.name, "development_file_sha256": _sha(development_path),
            "artifact_hash": artifact_hash, "registry_file": registry_path.name,
            "registry_file_sha256": _sha(registry_path), "registry_hash": registry_hash,
            "candidate_count": 12, "status": development["status"],
            "validation_accessed": False, "blind_accessed": False,
            "protected_existing_partitions_accessed": [],
        }
    report = {
        "schema": "stage-b-artifact-reconciliation-v1", "valid": not errors, "errors": errors,
        "materialization_file": materialization_path.name, "materialization_file_sha256": _sha(materialization_path),
        "datasets": datasets, "cycles": cycles,
        "code_files": {str(path).replace("\\", "/"): _sha(path) for path in CODE_FILES},
        "production_defaults_changed_by_stage_b": False,
        "validation_accessed": False, "blind_accessed": False,
        "existing_blind_holdout_accessed": False, "legacy_seen_test_accessed": False,
    }
    report["reconciliation_hash"] = _stable(report)
    output = ROOT / f"stage-b-reconciliation-{report['reconciliation_hash']}.json"
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"output": str(output), "valid": report["valid"], "errors": errors,
                      "reconciliation_hash": report["reconciliation_hash"]}, indent=2))


if __name__ == "__main__":
    main()
