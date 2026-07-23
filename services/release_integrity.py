"""Release metadata and runtime integrity checks."""
from __future__ import annotations

import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

from version import APP_VERSION, RELEASE_NAME


@dataclass(frozen=True, slots=True)
class IntegrityIssue:
    code: str
    message: str
    fatal: bool = True


@dataclass(frozen=True, slots=True)
class IntegrityReport:
    version: str
    release_name: str
    valid: bool
    issues: tuple[IntegrityIssue, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "release_name": self.release_name,
            "valid": self.valid,
            "issues": [asdict(issue) for issue in self.issues],
        }


def validate_release(root: Path | None = None, *, required_modules: Iterable[str] = ()) -> IntegrityReport:
    project_root = root or Path(__file__).resolve().parents[1]
    issues: list[IntegrityIssue] = []

    env_version = os.getenv("APP_VERSION", "").strip()
    if env_version and env_version != APP_VERSION:
        issues.append(IntegrityIssue("VERSION_MISMATCH", f"APP_VERSION={env_version} but code={APP_VERSION}"))

    for relative in ("README.md", ".env.example", "requirements.txt", "bot.py"):
        if not (project_root / relative).is_file():
            issues.append(IntegrityIssue("MISSING_RELEASE_FILE", f"Missing required file: {relative}"))

    for module in required_modules:
        module_path = project_root / (module.replace(".", "/") + ".py")
        package_path = project_root / module.replace(".", "/") / "__init__.py"
        if not module_path.is_file() and not package_path.is_file():
            issues.append(IntegrityIssue("MISSING_MODULE", f"Missing runtime module: {module}"))

    return IntegrityReport(
        version=APP_VERSION,
        release_name=RELEASE_NAME,
        valid=not any(issue.fatal for issue in issues),
        issues=tuple(issues),
    )
