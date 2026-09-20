from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path("research_artifacts/derivatives_alpha")


def main() -> int:
    primary_path = next(ROOT.glob("development-*.json"))
    cycle2_path = next(ROOT.glob("cycle2-4h-development-*.json"))
    primary = json.loads(primary_path.read_text(encoding="utf-8"))
    cycle2 = json.loads(cycle2_path.read_text(encoding="utf-8"))
    experiments = []
    for cycle, source in (("PRIMARY_1H", primary), ("CYCLE2_4H", cycle2)):
        for item in source["experiments"]:
            experiment = dict(item)
            experiment.update({
                "cycle": cycle,
                "provider_venue": "BINANCE",
                "materialization_id": "8886c1cd8d5e0b97",
                "split_identity": f"derivatives-alpha-{'primary-1h' if cycle == 'PRIMARY_1H' else 'cycle2-4h'}-v1:DERIV_DEV",
                "feature_schema_version": "positioning-state-v1",
                "cost_model": "ACTUAL_FUNDING_PLUS_BASE_HIGH_STRESS_FEE_AND_FRICTION",
                "validation_metrics": None,
                "blind_metrics": None,
            })
            experiments.append(experiment)
    result = {
        "schema": "derivatives-alpha-experiment-registry-v2",
        "materialization_id": "8886c1cd8d5e0b97",
        "experiment_count": len(experiments),
        "primary_variant_count": 12,
        "cycle2_variant_count": 9,
        "experiments": experiments,
        "development_qualifiers": [],
        "frozen_validation_candidate": None,
        "validation_accessed": False,
        "blind_accessed": False,
        "legacy_seen_test_accessed": False,
        "production_defaults_changed": False,
    }
    payload = json.dumps(result, indent=2, sort_keys=True, default=str) + "\n"
    identity = hashlib.sha256(payload.encode()).hexdigest()[:16]
    path = ROOT / f"final-experiment-registry-{identity}.json"
    path.write_text(payload, encoding="utf-8")
    print(path.as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
