"""Single-authority schema migration and production startup readiness gates."""
from __future__ import annotations

import logging
import os
import random
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator, TypeVar

from database import database as db


# Stable two-key PostgreSQL advisory-lock identity for Liquidity Vision schema DDL.
MIGRATION_LOCK_NAMESPACE = 0x4C565342  # "LVSB"
MIGRATION_LOCK_KEY = 1
REQUIRED_SCHEMA_TABLES = frozenset({
    "schema_migrations",
    "users",
    "signals",
    "distributed_leases",
    "forward_worker_health",
    "operational_worker_health",
    "scanner_outcome_labels",
})
RETRYABLE_POSTGRES_CODES = frozenset({"40001", "40P01", "55P03"})
_LOCAL_MIGRATION_LOCK = threading.Lock()
T = TypeVar("T")


class MigrationLockTimeout(RuntimeError):
    pass


class SchemaNotReadyError(RuntimeError):
    pass


@dataclass(frozen=True)
class SchemaStatus:
    ready: bool
    expected_version: int
    applied_version: int | None
    missing_tables: tuple[str, ...]
    reason: str


def expected_schema_version() -> int:
    return max(1, int(os.getenv("SCHEMA_VERSION", "2")))


def _positive_float(name: str, default: float, *, minimum: float = 0.01) -> float:
    return max(minimum, float(os.getenv(name, str(default))))


def _postgres_code(exc: BaseException) -> str | None:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        code = getattr(current, "pgcode", None)
        if code:
            return str(code)
        current = current.__cause__ or current.__context__
    return None


def _retryable_migration_error(exc: BaseException) -> bool:
    return _postgres_code(exc) in RETRYABLE_POSTGRES_CODES


def schema_status() -> SchemaStatus:
    """Inspect the committed schema marker and service-critical tables without DDL."""
    expected = expected_schema_version()
    try:
        with db.connect() as conn:
            if conn.postgres:
                timeout_ms = max(100, int(_positive_float(
                    "SCHEMA_CHECK_QUERY_TIMEOUT_SECONDS", 5.0,
                ) * 1000))
                conn.execute(
                    "SELECT set_config('statement_timeout', ?, true)",
                    (f"{timeout_ms}ms",),
                )
                placeholders = ",".join("?" for _ in REQUIRED_SCHEMA_TABLES)
                rows = conn.execute(
                    "SELECT table_name FROM information_schema.tables "
                    f"WHERE table_schema='public' AND table_name IN ({placeholders})",
                    tuple(sorted(REQUIRED_SCHEMA_TABLES)),
                ).fetchall()
            else:
                placeholders = ",".join("?" for _ in REQUIRED_SCHEMA_TABLES)
                rows = conn.execute(
                    f"SELECT name FROM sqlite_master WHERE type='table' AND name IN ({placeholders})",
                    tuple(sorted(REQUIRED_SCHEMA_TABLES)),
                ).fetchall()
            present = {str(row[0]) for row in rows}
            missing = tuple(sorted(REQUIRED_SCHEMA_TABLES - present))
            if missing:
                return SchemaStatus(False, expected, None, missing, "MISSING_REQUIRED_TABLES")
            row = conn.execute(
                "SELECT MAX(version) AS version FROM schema_migrations WHERE version>=?",
                (expected,),
            ).fetchone()
            applied = int(row[0]) if row and row[0] is not None else None
    except Exception as exc:
        code = _postgres_code(exc) or type(exc).__name__
        return SchemaStatus(False, expected, None, (), f"SCHEMA_CHECK_ERROR:{code}")
    if applied is None:
        return SchemaStatus(False, expected, None, (), "EXPECTED_VERSION_NOT_APPLIED")
    return SchemaStatus(True, expected, applied, (), "READY")


def migration_is_quiescent() -> bool:
    """Return true only when no authoritative migration session holds the lock."""
    if not db.USE_POSTGRES:
        return not _LOCAL_MIGRATION_LOCK.locked()
    try:
        conn = db.connect()
    except Exception:
        return False
    acquired = False
    try:
        row = conn.execute(
            "SELECT pg_try_advisory_lock(?, ?) AS acquired",
            (MIGRATION_LOCK_NAMESPACE, MIGRATION_LOCK_KEY),
        ).fetchone()
        acquired = bool(row and row[0])
        conn.commit()
        if not acquired:
            return False
        conn.execute(
            "SELECT pg_advisory_unlock(?, ?)",
            (MIGRATION_LOCK_NAMESPACE, MIGRATION_LOCK_KEY),
        )
        conn.commit()
        acquired = False
        return True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    finally:
        # Closing a PostgreSQL session releases a successfully acquired lock
        # even if the explicit unlock failed.
        conn.close()


@contextmanager
def migration_lock(*, timeout_seconds: float | None = None) -> Iterator[None]:
    """Serialize schema mutation across processes using a session advisory lock."""
    timeout = timeout_seconds if timeout_seconds is not None else _positive_float(
        "MIGRATION_LOCK_TIMEOUT_SECONDS", 300.0,
    )
    poll = _positive_float("MIGRATION_LOCK_POLL_SECONDS", 1.0)
    deadline = time.monotonic() + timeout
    logging.info("MIGRATION_LOCK_WAIT timeout_seconds=%.1f", timeout)

    if not db.USE_POSTGRES:
        acquired = _LOCAL_MIGRATION_LOCK.acquire(timeout=timeout)
        if not acquired:
            raise MigrationLockTimeout(
                f"MIGRATION_LOCK_TIMEOUT backend=sqlite timeout_seconds={timeout:.1f}"
            )
        logging.info("MIGRATION_LOCK_ACQUIRED backend=sqlite")
        try:
            yield
        finally:
            _LOCAL_MIGRATION_LOCK.release()
            logging.info("MIGRATION_LOCK_RELEASED backend=sqlite")
        return

    conn = db.connect()
    acquired = False
    try:
        while True:
            row = conn.execute(
                "SELECT pg_try_advisory_lock(?, ?) AS acquired",
                (MIGRATION_LOCK_NAMESPACE, MIGRATION_LOCK_KEY),
            ).fetchone()
            acquired = bool(row and row[0])
            conn.commit()
            if acquired:
                logging.info(
                    "MIGRATION_LOCK_ACQUIRED backend=postgresql namespace=%s key=%s",
                    MIGRATION_LOCK_NAMESPACE,
                    MIGRATION_LOCK_KEY,
                )
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MigrationLockTimeout(
                    f"MIGRATION_LOCK_TIMEOUT backend=postgresql timeout_seconds={timeout:.1f}"
                )
            time.sleep(min(poll, remaining))
        yield
    finally:
        if acquired:
            try:
                conn.rollback()
                conn.execute(
                    "SELECT pg_advisory_unlock(?, ?)",
                    (MIGRATION_LOCK_NAMESPACE, MIGRATION_LOCK_KEY),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                logging.exception("MIGRATION_LOCK_RELEASE_FAILED")
            else:
                logging.info("MIGRATION_LOCK_RELEASED backend=postgresql")
        conn.close()


def run_authoritative_migration(
    migration: Callable[[], T],
    *,
    ready_check: Callable[[], SchemaStatus] = schema_status,
) -> tuple[SchemaStatus, T]:
    """Run the complete migration command under the singleton lock and retry."""
    max_attempts = max(1, int(os.getenv("MIGRATION_MAX_ATTEMPTS", "3")))
    retry_base = _positive_float("MIGRATION_RETRY_BASE_SECONDS", 1.0)
    for attempt in range(1, max_attempts + 1):
        try:
            with migration_lock():
                logging.info("MIGRATION_STARTED attempt=%s/%s", attempt, max_attempts)
                result = migration()
                status = ready_check()
                if not status.ready:
                    raise SchemaNotReadyError(
                        f"SCHEMA_NOT_READY_AFTER_MIGRATION reason={status.reason} "
                        f"expected_version={status.expected_version}"
                    )
                logging.info(
                    "MIGRATION_COMPLETE version=%s attempt=%s/%s",
                    status.applied_version,
                    attempt,
                    max_attempts,
                )
                logging.info(
                    "SCHEMA_READY service=migration version=%s",
                    status.applied_version,
                )
                return status, result
        except Exception as exc:
            if not _retryable_migration_error(exc) or attempt >= max_attempts:
                raise
            delay = retry_base * (2 ** (attempt - 1))
            delay += random.uniform(0.0, min(0.25, retry_base / 2))
            logging.warning(
                "MIGRATION_RETRY attempt=%s/%s pgcode=%s delay_seconds=%.2f",
                attempt,
                max_attempts,
                _postgres_code(exc),
                delay,
            )
            time.sleep(delay)
    raise AssertionError("unreachable")


def migrate_schema(
    migration: Callable[[], None] = db.create_tables,
    *,
    ready_check: Callable[[], SchemaStatus] = schema_status,
) -> SchemaStatus:
    """Run idempotent schema DDL under the authoritative migration contract."""
    status, _ = run_authoritative_migration(migration, ready_check=ready_check)
    return status


def wait_for_schema_ready(*, service_name: str) -> SchemaStatus:
    """Wait for the authoritative migration without ever issuing service DDL."""
    timeout = _positive_float("SCHEMA_READY_TIMEOUT_SECONDS", 600.0)
    poll = _positive_float("SCHEMA_READY_POLL_SECONDS", 1.0)
    deadline = time.monotonic() + timeout
    last_reason: str | None = None
    while True:
        status = (
            schema_status()
            if migration_is_quiescent()
            else SchemaStatus(
                False,
                expected_schema_version(),
                None,
                (),
                "MIGRATION_IN_PROGRESS",
            )
        )
        if status.ready:
            logging.info(
                "SCHEMA_READY service=%s version=%s",
                service_name,
                status.applied_version,
            )
            return status
        if status.reason != last_reason:
            logging.info(
                "SCHEMA_NOT_READY_WAIT service=%s reason=%s expected_version=%s",
                service_name,
                status.reason,
                status.expected_version,
            )
            last_reason = status.reason
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SchemaNotReadyError(
                f"SCHEMA_NOT_READY service={service_name} reason={status.reason} "
                f"timeout_seconds={timeout:.1f}"
            )
        jitter = random.uniform(0.0, min(0.1, poll / 4))
        time.sleep(min(poll + jitter, remaining))


def initialize_service_database(*, service_name: str) -> SchemaStatus:
    """Create locally for SQLite; production PostgreSQL services only wait/read."""
    default_mode = "wait" if db.USE_POSTGRES else "create"
    mode = os.getenv("SCHEMA_STARTUP_MODE", default_mode).strip().lower()
    if mode == "wait":
        return wait_for_schema_ready(service_name=service_name)
    if mode != "create":
        raise RuntimeError(f"unsupported SCHEMA_STARTUP_MODE={mode!r}")
    if db.USE_POSTGRES:
        raise RuntimeError(
            "PostgreSQL service startup cannot own DDL; run "
            "python -m tools.run_product_migrations"
        )
    db.create_tables()
    status = schema_status()
    if not status.ready:
        raise SchemaNotReadyError(
            f"SCHEMA_NOT_READY service={service_name} reason={status.reason}"
        )
    logging.info("SCHEMA_READY service=%s version=%s", service_name, status.applied_version)
    return status
