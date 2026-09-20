from __future__ import annotations

import hashlib
import json

from services.derivatives_alpha_lab import ROOT
from services.derivatives_cycle2_lab import run_cycle2_development


def main() -> int:
    result = run_cycle2_development()
    payload = json.dumps(result, indent=2, sort_keys=True, default=str) + "\n"
    identity = hashlib.sha256(payload.encode()).hexdigest()[:16]
    path = ROOT / f"cycle2-4h-development-{identity}.json"
    path.write_text(payload, encoding="utf-8")
    print(path.as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
