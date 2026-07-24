from __future__ import annotations

import asyncio
import logging
import os

from services.copy_trading import CopyTradingService


class CopyExecutionWorker:
    def __init__(self, interval_seconds: int | None = None):
        self.interval_seconds = max(30, interval_seconds or int(os.getenv("COPY_EXECUTION_INTERVAL", "60")))
        self.service = CopyTradingService()
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def check_once(self) -> dict[str, int]:
        def run_cycle() -> dict[str, int]:
            totals = self.service.sync_all()
            queue_results = self.service.execution_queue.drain(limit=25)
            totals["queue_processed"] = len(queue_results)
            totals["queue_executed"] = sum(1 for item in queue_results if item.status.value == "EXECUTED")
            totals["queue_failed"] = sum(1 for item in queue_results if item.status.value in {"FAILED", "REJECTED"})
            return totals

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
