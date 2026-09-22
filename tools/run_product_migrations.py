"""Explicit one-off product migration/backfill command; never a web startup hook."""
from __future__ import annotations

import json
import os

from database.database import create_tables
from database.schema_management import run_authoritative_migration
from services.historical_execution_migration import HistoricalExecutionMigrationService
from services.operational_retention import OperationalRetentionService
from services.trade_memory import TradeMemoryService


def run() -> dict[str, object]:
    def migrate_and_backfill() -> dict[str, object]:
        ddl_lock_timeout_ms = max(
            1000,
            int(float(os.getenv("MIGRATION_DDL_LOCK_TIMEOUT_SECONDS", "15")) * 1000),
        )
        statement_timeout_ms = max(
            1000,
            int(float(os.getenv("MIGRATION_STATEMENT_TIMEOUT_SECONDS", "120")) * 1000),
        )
        create_tables(
            lock_timeout_ms=ddl_lock_timeout_ms,
            statement_timeout_ms=statement_timeout_ms,
        )
        migration = HistoricalExecutionMigrationService().run(
            batch_size=int(os.getenv("HISTORICAL_MIGRATION_BATCH_SIZE", "500")),
        )
        backfill = TradeMemoryService().backfill(
            limit=int(os.getenv("MEMORY_BACKFILL_LIMIT", "500")),
        )
        retention = OperationalRetentionService().run()
        return {
            "historical_execution": migration.as_dict(),
            "trade_memory": backfill,
            "operational_retention": retention,
        }

    schema, result = run_authoritative_migration(migrate_and_backfill)
    return {
        "schema": {
            "ready": schema.ready,
            "expected_version": schema.expected_version,
            "applied_version": schema.applied_version,
        },
        **result,
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, sort_keys=True, default=str))
