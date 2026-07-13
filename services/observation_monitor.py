from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid

from database.database import acquire_lease, release_lease, runtime_finished, runtime_started
from database.observation_history import ObservationHistory
from services.analysis_runtime import run_analysis
from services.analyzer import Analyzer
from services.market import Market
from services.signal_recorder import SignalRecorder


class ObservationMonitor:
    worker_name = "observation_monitor"

    def __init__(self, bot=None, interval_seconds: int | None = None):
        self.bot = bot
        self.interval_seconds = interval_seconds or int(os.getenv("OBSERVATION_CHECK_INTERVAL", "300"))
        self.history = ObservationHistory()
        self.market = Market()
        self.analyzer = Analyzer()
        self.recorder = SignalRecorder()
        self._stop = asyncio.Event()
        self.owner_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"

    def stop(self) -> None:
        self._stop.set()

    async def check_once(self) -> dict[str, int | bool]:
        if not acquire_lease(self.worker_name, self.owner_id, max(self.interval_seconds * 2, 180)):
            return {"skipped": True, "processed": 0, "errors": 0, "promoted": 0}
        runtime_started(self.worker_name)
        processed = errors = promoted = 0
        try:
            for observation in self.history.pending(limit=40):
                processed += 1
                try:
                    df = await asyncio.wait_for(
                        self.market.get_klines(observation["symbol"], observation["timeframe"]), timeout=35
                    )
                    analysis = await asyncio.wait_for(run_analysis(self.analyzer, df), timeout=45)
                    signal_id = self.recorder.record(
                        symbol=observation["symbol"], timeframe=observation["timeframe"], analysis=analysis,
                        owner_telegram_id=observation["owner_telegram_id"],
                        notification_chat_id=observation.get("notification_chat_id"),
                    )
                    if signal_id:
                        promoted += 1
                    if signal_id and self.bot and observation.get("notification_chat_id"):
                        await self.bot.send_message(
                            observation["notification_chat_id"],
                            f"🔔 <b>{observation['symbol']} observation promoted</b>\n\n"
                            f"Signal ID: <code>{signal_id}</code>\n"
                            f"Status: {analysis.get('execution_status')}\n"
                            f"Recommendation: {analysis.get('recommendation')}",
                        )
                except Exception:
                    errors += 1
                    logging.exception("Observation monitor failed for %s", observation.get("symbol"))
            runtime_finished(self.worker_name, processed=processed, errors=errors, details={"promoted": promoted})
            return {"skipped": False, "processed": processed, "errors": errors, "promoted": promoted}
        except Exception as exc:
            runtime_finished(self.worker_name, processed=processed, errors=errors + 1, error=str(exc))
            raise
        finally:
            release_lease(self.worker_name, self.owner_id)

    async def run_forever(self) -> None:
        logging.info("ObservationMonitor started: interval=%ss", self.interval_seconds)
        while not self._stop.is_set():
            try:
                await self.check_once()
            except Exception:
                logging.exception("ObservationMonitor cycle failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                pass
        logging.info("ObservationMonitor stopped")
