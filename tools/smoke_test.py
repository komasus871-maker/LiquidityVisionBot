"""Production smoke test. Run: python tools/smoke_test.py"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    checks: dict[str, object] = {}
    try:
        from database.database import create_tables, ping_database
        from services.runtime_diagnostics import collect_runtime_diagnostics

        create_tables()
        checks["database"] = ping_database()
        report = collect_runtime_diagnostics()
        checks["diagnostics_status"] = report["status"]
        checks["database_backend"] = report["database_backend"]
        checks["integrity"] = report["integrity"]
        checks["counts"] = report["counts"]
        ok = bool(checks["database"].get("ok")) and bool(report["integrity"].get("ok"))
    except Exception as exc:
        checks["error"] = f"{type(exc).__name__}: {exc}"
        ok = False

    print(json.dumps({"ok": ok, "checks": checks}, ensure_ascii=False, indent=2, default=str))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
