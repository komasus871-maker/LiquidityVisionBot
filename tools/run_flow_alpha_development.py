from __future__ import annotations

import hashlib
import json

from services.flow_alpha_lab import ROOT, run_development


def main() -> int:
    result = run_development()
    payload = json.dumps(result, indent=2, sort_keys=True, default=str) + "\n"
    identity = hashlib.sha256(payload.encode()).hexdigest()[:16]
    path = ROOT / f"directional-development-{identity}.json"
    path.write_text(payload, encoding="utf-8")
    print(path.as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
