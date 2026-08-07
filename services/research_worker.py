from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid

from database.database import acquire_lease, release_lease, runtime_finished, runtime_started
from services.research_engine import ResearchEngine


class ResearchWorker:
    """Bounded, lease-protected research projection worker.

    Research failures are recorded and isolated from signal tracking, deterministic
    policy, copy execution, and portfolio accounting.
    """

    worker_name = "research_engine"

    def __init__(self, interval_seconds: int | None = None):
        self.interval_seconds = max(60, interval_seconds or int(os.getenv("RESEARCH_INTERVAL_SECONDS", "300")))
        self.batch_limit = max(10, min(int(os.getenv("RESEARCH_BATCH_LIMIT", "200")), 1000))
        self.owner_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self.engine = ResearchEngine()
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def check_once(self) -> dict[str, int | bool]:
        def run_cycle() -> dict[str, int | bool]:
            ttl = max(self.interval_seconds * 2, 180)
            if not acquire_lease(self.worker_name, self.owner_id, ttl):
                return {"skipped": True, "captured": 0, "outcomes_attached": 0}
            runtime_started(self.worker_name)
            try:
                result = self.engine.run_cycle(self.batch_limit)
                runtime_finished(
                    self.worker_name,
                    processed=int(result["captured"]) + int(result["outcomes_attached"]),
                    errors=0,
                    details=result,
                )
                return {"skipped": False, **result}
            except Exception as exc:
                runtime_finished(
                    self.worker_name, processed=0, errors=1,
                    error=f"{type(exc).__name__}: {exc}",
                )
                raise
            finally:
                release_lease(self.worker_name, self.owner_id)

        return await asyncio.to_thread(run_cycle)

    async def run_forever(self) -> None:
        logging.info(
            "ResearchWorker started: interval=%ss batch=%s",
            self.interval_seconds, self.batch_limit,
        )
        while not self._stop.is_set():
            try:
                result = await self.check_once()
                if not result.get("skipped") and any(
                    int(result.get(key) or 0) for key in ("captured", "outcomes_attached")
                ):
                    logging.info("Research projection cycle: %s", result)
            except Exception:
                logging.exception("Research projection cycle failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                pass
        logging.info("ResearchWorker stopped")
