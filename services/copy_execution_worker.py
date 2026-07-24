from __future__ import annotations

import asyncio
import logging
import os
import socket

from services.copy_trading import CopyTradingService
from database.database import runtime_finished, runtime_started


class CopyExecutionWorker:
    def __init__(self, interval_seconds: int | None = None):
        self.interval_seconds = max(30, interval_seconds or int(os.getenv("COPY_EXECUTION_INTERVAL", "60")))
        self.worker_id = os.getenv("COPY_WORKER_ID", f"copy-execution:{socket.gethostname()}:{os.getpid()}")
        self.service = CopyTradingService()
        # Ensure queue claims are attributed to this runtime instance.
        self.service.execution_queue.engine.worker_id = self.worker_id
        self.service.execution_queue.engine.lease_seconds = max(30, int(os.getenv("COPY_EXECUTION_LEASE_SECONDS", "180")))
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def check_once(self) -> dict[str, int]:
        def run_cycle() -> dict[str, int]:
            runtime_started(self.worker_id)
            try:
                totals = self.service.sync_all()
                recovery = self.service.execution_queue.journal.recover_expired_claims(limit=100)
                queue_results = self.service.execution_queue.drain(limit=25)
                projected = sum(
                    1 for item in queue_results
                    if item.status.value == "EXECUTED"
                    and self.service.project_execution(item.idempotency_key)
                )
                totals["queue_processed"] = len(queue_results)
                totals["queue_executed"] = sum(1 for item in queue_results if item.status.value == "EXECUTED")
                totals["legacy_projections"] = projected
                totals["queue_retry_wait"] = sum(1 for item in queue_results if item.status.value == "RETRY_WAIT")
                totals["queue_failed"] = sum(1 for item in queue_results if item.status.value in {"FAILED", "REJECTED", "DEAD_LETTER"})
                totals["leases_recovered"] = recovery["recovered"]
                totals["dead_lettered"] = recovery["dead_lettered"]
                runtime_finished(
                    self.worker_id, processed=totals["queue_processed"], errors=totals["queue_failed"],
                    details=totals,
                )
                return totals
            except Exception as exc:
                runtime_finished(self.worker_id, processed=0, errors=1, error=f"{type(exc).__name__}: {exc}")
                raise

        return await asyncio.to_thread(run_cycle)

    async def run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                result = await self.check_once()
                if any(result.values()):
                    logging.info("Paper copy sync: %s", result)
            except Exception:
                logging.exception("Paper copy execution cycle failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                pass
