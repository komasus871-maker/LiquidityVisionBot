"""Runtime ownership and health for the non-LIVE product worker."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from database.database import connect, get_runtime_states, runtime_finished, runtime_started
from services.operational_retention import OperationalRetentionService


OPERATIONAL_WORKER_NAME = "operational_product_worker"
OPERATIONAL_COMPONENTS = (
    "signal_tracker",
    "observation_monitor",
    "watch_engine",
    "copy_execution",
    "ai_shadow",
    "research_engine",
    "pump_dump_monitor",
    "operational_maintenance",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def current_rss_mb() -> float | None:
    """Return current RSS on Render/Linux without adding a process dependency."""
    try:
        statm = Path("/proc/self/statm").read_text(encoding="ascii").split()
        return round(int(statm[1]) * int(os.sysconf("SC_PAGE_SIZE")) / 1_048_576, 2)
    except (AttributeError, IndexError, OSError, ValueError):
        return None


class OperationalHealthRepository:
    worker_name = OPERATIONAL_WORKER_NAME

    def heartbeat(
        self, *, instance_id: str, state: str, started_at: str,
        child_states: dict[str, Any], last_error: str | None = None,
    ) -> None:
        now = utc_now()
        with connect() as connection:
            connection.execute(
                """INSERT INTO operational_worker_health(
                    worker_name,instance_id,state,started_at,heartbeat_at,
                    child_states_json,rss_mb,last_error,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(worker_name) DO UPDATE SET
                    instance_id=excluded.instance_id,state=excluded.state,
                    started_at=excluded.started_at,heartbeat_at=excluded.heartbeat_at,
                    child_states_json=excluded.child_states_json,rss_mb=excluded.rss_mb,
                    last_error=excluded.last_error,updated_at=excluded.updated_at""",
                (self.worker_name, instance_id, state, started_at, now,
                 json.dumps(child_states, sort_keys=True, default=str), current_rss_mb(),
                 last_error, now),
            )

    def health(self) -> dict[str, Any] | None:
        with connect() as connection:
            row = connection.execute(
                "SELECT * FROM operational_worker_health WHERE worker_name=?",
                (self.worker_name,),
            ).fetchone()
        if not row:
            return None
        value = dict(row)
        try:
            value["child_states"] = json.loads(value.pop("child_states_json") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            value["child_states"] = {}
            value.pop("child_states_json", None)
        return value


def child_runtime_states(
    supervisor_states: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    allowed = set(OPERATIONAL_COMPONENTS)
    result: dict[str, Any] = {}
    for row in get_runtime_states():
        item = dict(row)
        name = str(item.get("worker_name") or "")
        normalized = {
            "copy-execution": "copy_execution",
            "pump-dump-market-alert-monitor": "pump_dump_monitor",
        }.get(name, name)
        if name.startswith("copy-execution:"):
            normalized = "copy_execution"
        if normalized not in allowed:
            continue
        child = {
            "last_success_at": item.get("last_success_at"),
            "last_started_at": item.get("last_started_at"),
            "last_finished_at": item.get("last_finished_at"),
            "last_error": item.get("last_error"),
            "processed_count": int(item.get("processed_count") or 0),
            "error_count": int(item.get("error_count") or 0),
        }
        child.update((supervisor_states or {}).get(normalized, {}))
        if normalized == "pump_dump_monitor":
            try:
                details = json.loads(item.get("details_json") or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                details = {}
            child["scanner"] = {
                key: details.get(key) for key in (
                    "status", "universe", "universe_target", "successfully_fetched",
                    "failed_symbol_count", "baseline_ready_symbols", "shortlisted_symbols",
                    "deep_enrichment_symbols", "global_events_created", "active_episodes",
                    "cycle_duration_seconds", "labels_pending", "labels_complete",
                    "cohorts_sample_ready", "pipeline_timestamps", "current_stage",
                    "cycle_started_at", "cycle_completed_at", "enrichment_status",
                    "forward_microstructure_state", "enrichment_requested_symbols",
                    "provider_coverage", "viable_provider_count", "providers",
                )
            }
        result[normalized] = child
    for name, state in (supervisor_states or {}).items():
        if name in allowed and name not in result:
            result[name] = dict(state)
    return result


class OperationalMaintenanceWorker:
    worker_name = "operational_maintenance"

    def __init__(self) -> None:
        self.interval_seconds = max(
            3_600, int(os.getenv("OPERATIONAL_MAINTENANCE_INTERVAL_SECONDS", "21600")),
        )
        self.initial_delay_seconds = max(
            0, int(os.getenv("OPERATIONAL_MAINTENANCE_INITIAL_DELAY_SECONDS", "300")),
        )
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def check_once(self) -> dict[str, Any]:
        runtime_started(self.worker_name)
        try:
            result = await asyncio.to_thread(OperationalRetentionService().run)
            deleted = sum(int(value) for value in (result.get("deleted") or {}).values())
            runtime_finished(self.worker_name, processed=deleted, errors=0, details=result)
            return result
        except Exception as exc:
            runtime_finished(
                self.worker_name, processed=0, errors=1,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise

    async def run_forever(self) -> None:
        if self.initial_delay_seconds:
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.initial_delay_seconds)
                return
            except asyncio.TimeoutError:
                pass
        while not self._stop.is_set():
            try:
                await self.check_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.exception("Operational maintenance cycle failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                pass
