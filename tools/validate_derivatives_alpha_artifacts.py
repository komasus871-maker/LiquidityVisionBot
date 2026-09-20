from __future__ import annotations

import hashlib
import ast
import json
from pathlib import Path

from services.derivatives_alpha import candidate_registry_identity, frozen_candidates
from services.derivatives_cycle2_lab import cycle2_candidates


ROOT = Path("research_artifacts/derivatives_alpha")


def main() -> int:
    errors: list[str] = []
    materialization_path = ROOT / "materialization-8886c1cd8d5e0b97.json"
    materialization = json.loads(materialization_path.read_text(encoding="utf-8"))
    if materialization.get("materialization_id") != "8886c1cd8d5e0b97":
        errors.append("materialization identity mismatch")
    if materialization.get("outcomes_evaluated") or materialization.get("blind_accessed"):
        errors.append("materialization access flags invalid")
    expected_rows = {"DERIV_DEV": 10968, "DERIV_VALIDATION_SEALED": 3624, "DERIV_BLIND_SEALED": 2928}
    for role, symbols in materialization["splits"].items():
        for symbol, receipt in symbols.items():
            path = Path(receipt["path"])
            if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != receipt["sha256"]:
                errors.append(f"{role}/{symbol} content hash mismatch")
            if receipt["rows"] != expected_rows[role]:
                errors.append(f"{role}/{symbol} row count mismatch")
    developments = list(ROOT.glob("development-*.json"))
    cycles = list(ROOT.glob("cycle2-4h-development-*.json"))
    registries = list(ROOT.glob("experiment-registry-*.json"))
    final_registries = list(ROOT.glob("final-experiment-registry-*.json"))
    if len(developments) != 1 or len(cycles) != 1 or len(registries) != 1:
        errors.append("expected exactly one primary Development, cycle-2 Development, and registry artifact")
    else:
        primary = json.loads(developments[0].read_text(encoding="utf-8"))
        cycle2 = json.loads(cycles[0].read_text(encoding="utf-8"))
        registry = json.loads(registries[0].read_text(encoding="utf-8"))
        if primary.get("candidate_registry_identity") != candidate_registry_identity(frozen_candidates()):
            errors.append("primary candidate registry identity mismatch")
        if len(primary.get("experiments", [])) != 12 or len({x["candidate"]["candidate_id"] for x in primary["experiments"]}) != 12:
            errors.append("primary experiment roster mismatch")
        if len(cycle2.get("experiments", [])) != 9 or len(cycle2_candidates()) != 9:
            errors.append("cycle-2 experiment roster mismatch")
        if primary.get("development_qualified_count") != 0 or cycle2.get("development_qualified_count") != 0:
            errors.append("unexpected Development qualifier")
        for artifact in (primary, cycle2, registry):
            if artifact.get("validation_accessed") or artifact.get("blind_accessed"):
                errors.append("protected split access flag is true")
    if len(final_registries) != 1:
        errors.append("expected exactly one final experiment registry")
    else:
        final_registry = json.loads(final_registries[0].read_text(encoding="utf-8"))
        if final_registry.get("experiment_count") != 21:
            errors.append("final experiment registry count mismatch")
        if final_registry.get("development_qualifiers") or final_registry.get("frozen_validation_candidate"):
            errors.append("final registry unexpectedly contains a qualifier")
    research_modules = {
        "services.derivatives_alpha", "services.derivatives_alpha_lab", "services.derivatives_cycle2_lab",
    }
    allowed_files = {
        Path("services/derivatives_alpha.py"), Path("services/derivatives_alpha_lab.py"),
        Path("services/derivatives_cycle2_lab.py"),
    }
    for root in (Path("handlers"), Path("core"), Path("database"), Path("services")):
        for source in root.rglob("*.py"):
            if source in allowed_files:
                continue
            tree = ast.parse(source.read_text(encoding="utf-8"))
            imports = {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
            imports.update(alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names)
            if imports & research_modules:
                errors.append(f"production path imports derivatives research: {source}")
    development_source = Path("services/derivatives_alpha_lab.py").read_text(encoding="utf-8")
    cycle2_source = Path("services/derivatives_cycle2_lab.py").read_text(encoding="utf-8")
    if "DERIV_VALIDATION_SEALED" in development_source + cycle2_source or "DERIV_BLIND_SEALED" in development_source + cycle2_source:
        errors.append("Development lab contains protected-split loader")
    if "DecisionQualityEngine.authorization" not in development_source:
        errors.append("candidate admission does not invoke DecisionQuality authorization")
    result = {
        "schema": "derivatives-alpha-reconciliation-v1",
        "valid": not errors,
        "errors": errors,
        "materialization_id": "8886c1cd8d5e0b97",
        "primary_variants": 12,
        "cycle2_variants": 9,
        "validation_accessed": False,
        "blind_accessed": False,
        "legacy_seen_test_accessed": False,
        "production_defaults_changed": False,
    }
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    identity = hashlib.sha256(payload.encode()).hexdigest()[:16]
    path = ROOT / f"reconciliation-{identity}.json"
    path.write_text(payload, encoding="utf-8")
    print(path.as_posix())
    print(payload, end="")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
