from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone

from database.database import connect


POLICY_VERSION = "operational-retention-v2"


class OperationalRetentionService:
    """Audited retention for explicitly ephemeral product telemetry only."""

    TARGETS = {
        "ai_provider_request_events": "PROVIDER_EVENT_RETENTION_DAYS",
        "feature_usage_events": "FEATURE_USAGE_RETENTION_DAYS",
        "intelligence_alert_events": "ALERT_EVENT_RETENTION_DAYS",
        "microstructure_aggregates": "MICROSTRUCTURE_RETENTION_DAYS",
        "market_source_snapshots": "DERIVATIVES_RETENTION_DAYS",
    }

    @staticmethod
    def _days(variable: str) -> int:
        try:
            return max(30, min(3650, int(os.getenv(variable, "90"))))
        except ValueError:
            return 90

    def run(self) -> dict[str, object]:
        enabled = os.getenv("OPERATIONAL_RETENTION_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
        if not enabled:
            return {"status": "DISABLED_BY_CONFIGURATION", "deleted": {}}
        started = datetime.now(timezone.utc)
        run_key = str(uuid.uuid4())
        cutoffs = {table: (started - timedelta(days=self._days(variable))).isoformat()
                   for table, variable in self.TARGETS.items()}
        deleted: dict[str, int] = {}
        status, error = "COMPLETE", None
        try:
            with connect() as conn:
                for table, cutoff in cutoffs.items():
                    cursor = conn.execute(f"DELETE FROM {table} WHERE created_at<?", (cutoff,))
                    deleted[table] = max(0, int(cursor.rowcount or 0))
        except Exception as exc:
            status, error = "FAILED", str(exc)[:500]
        completed = datetime.now(timezone.utc).isoformat()
        with connect() as conn:
            conn.execute("""INSERT INTO operational_retention_runs(run_key,policy_version,status,
                deleted_counts_json,cutoffs_json,error_text,started_at,completed_at)
                VALUES(?,?,?,?,?,?,?,?)""",
                (run_key, POLICY_VERSION, status, json.dumps(deleted, sort_keys=True),
                 json.dumps(cutoffs, sort_keys=True), error, started.isoformat(), completed))
        return {"status": status, "deleted": deleted, "cutoffs": cutoffs,
                "policy_version": POLICY_VERSION}
