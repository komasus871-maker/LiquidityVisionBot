from __future__ import annotations

import asyncio
import logging
import os

from database.observation_history import ObservationHistory
from services.market import Market
from services.analyzer import Analyzer
from services.signal_recorder import SignalRecorder


class ObservationMonitor:
    def __init__(self, bot=None, interval_seconds: int | None = None):
        self.bot = bot
        self.interval_seconds = interval_seconds or int(os.getenv("OBSERVATION_CHECK_INTERVAL", "300"))
        self.history = ObservationHistory()
        self.market = Market()
        self.analyzer = Analyzer()
        self.recorder = SignalRecorder()

    async def check_once(self) -> None:
        for observation in self.history.pending(limit=40):
            try:
                df = await self.market.get_klines(observation["symbol"], observation["timeframe"])
                analysis = self.analyzer.analyze(df)
                signal_id = self.recorder.record(
                    symbol=observation["symbol"], timeframe=observation["timeframe"], analysis=analysis,
                    owner_telegram_id=observation["owner_telegram_id"],
                    notification_chat_id=observation.get("notification_chat_id"),
                )
                if signal_id and self.bot and observation.get("notification_chat_id"):
                    await self.bot.send_message(
                        observation["notification_chat_id"],
                        f"🔔 <b>{observation['symbol']} observation promoted</b>\n\nSignal ID: <code>{signal_id}</code>\nStatus: {analysis.get('execution_status')}\nRecommendation: {analysis.get('recommendation')}",
                    )
            except Exception as exc:
                logging.warning("Observation monitor failed for %s: %s", observation.get("symbol"), exc)

    async def run_forever(self) -> None:
        while True:
            await self.check_once()
            await asyncio.sleep(self.interval_seconds)
