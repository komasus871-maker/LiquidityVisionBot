from __future__ import annotations

import hashlib
import json
from pathlib import Path

from services.derivatives_alpha_lab import ROOT, run_development


def main() -> int:
    result = run_development()
    payload = json.dumps(result, indent=2, sort_keys=True, default=str) + "\n"
    identity = hashlib.sha256(payload.encode()).hexdigest()[:16]
    path = ROOT / f"development-{identity}.json"
    path.write_text(payload, encoding="utf-8")
    registry = {
        "schema": "derivatives-alpha-experiment-registry-v1",
        "source_artifact": path.as_posix(),
        "candidate_registry_identity": result["candidate_registry_identity"],
        "experiments": result["experiments"],
        "validation_accessed": False,
        "blind_accessed": False,
    }
    registry_payload = json.dumps(registry, indent=2, sort_keys=True, default=str) + "\n"
    registry_id = hashlib.sha256(registry_payload.encode()).hexdigest()[:16]
    registry_path = ROOT / f"experiment-registry-{registry_id}.json"
    registry_path.write_text(registry_payload, encoding="utf-8")
    print(path.as_posix())
    print(registry_path.as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
