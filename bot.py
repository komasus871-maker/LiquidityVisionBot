from __future__ import annotations

import asyncio
import logging
import os
import signal

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import BOT_TOKEN
from database.database import create_tables, database_backend, persistent_database, ping_database
from handlers.admin import router as admin_router
from handlers.analyze import router as analyze_router
from handlers.fear import router as fear_router
from handlers.help import router as help_router
from handlers.journal import router as journal_router
from handlers.market import router as market_router
from handlers.menu import router as menu_router
from handlers.news import router as news_router
from handlers.premium import router as premium_router
from handlers.price import router as price_router
from handlers.profile import router as profile_router
from handlers.scanner import router as scanner_router
from handlers.start import router as start_router
from services.observation_monitor import ObservationMonitor
from services.signal_tracker import SignalTracker
from services.watch_engine import WatchEngine
from services.webhook_server import WebhookServer
from services.trade_memory import TradeMemoryService

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(message)s",
)


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.include_router(admin_router)
    dp.include_router(start_router)
    dp.include_router(help_router)
    dp.include_router(price_router)
    dp.include_router(analyze_router)
    dp.include_router(profile_router)
    dp.include_router(scanner_router)
    dp.include_router(fear_router)
    dp.include_router(market_router)
    dp.include_router(news_router)
    dp.include_router(journal_router)
    dp.include_router(premium_router)
    dp.include_router(menu_router)
    return dp


def deployment_mode() -> str:
    configured = os.getenv("BOT_MODE", "auto").strip().lower()
    if configured in {"webhook", "polling"}:
        return configured
    on_render = bool(
        os.getenv("RENDER")
        or os.getenv("RENDER_SERVICE_NAME")
        or os.getenv("RENDER_EXTERNAL_URL")
    )
    return "webhook" if on_render else "polling"


async def _stop_workers(workers: list[object], tasks: list[asyncio.Task]) -> None:
    for worker in workers:
        stop = getattr(worker, "stop", None)
        if callable(stop):
            stop()
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def main() -> None:
    logging.info("Creating database...")
    create_tables()
    backfill = TradeMemoryService().backfill(limit=int(os.getenv("MEMORY_BACKFILL_LIMIT", "500")))
    logging.info("AI memory backfill: scanned=%s created=%s", backfill["scanned"], backfill["created"])
    db_health = ping_database()
    logging.info("Database ready: backend=%s persistent=%s latency_ms=%s", database_backend(), persistent_database(), db_health.get("latency_ms"))

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = build_dispatcher()

    tracker = SignalTracker(interval_seconds=int(os.getenv("SIGNAL_CHECK_INTERVAL", "60")), bot=bot)
    observation_monitor = ObservationMonitor(bot=bot)
    watch_engine = WatchEngine(bot=bot)
    workers = [tracker, observation_monitor, watch_engine]
    worker_tasks = [
        asyncio.create_task(tracker.run_forever(), name="signal-tracker"),
        asyncio.create_task(observation_monitor.run_forever(), name="observation-monitor"),
        asyncio.create_task(watch_engine.run_forever(), name="watch-engine"),
    ]

    mode = deployment_mode()
    logging.info("Liquidity Vision starting in %s mode", mode)

    webhook_server: WebhookServer | None = None
    try:
        await dp.emit_startup(bot=bot)
        if mode == "webhook":
            async def maintenance_cycle() -> dict[str, object]:
                # Free Render sleeps while idle. An external cron can wake the
                # service and run one complete, lease-protected monitor cycle.
                watch_result = await watch_engine.check_once()
                observation_result = await observation_monitor.check_once()
                tracker_result = await tracker.check_once()
                return {
                    "database_backend": database_backend(),
                    "persistent_database": persistent_database(),
                    "watch_engine": watch_result,
                    "observation_monitor": observation_result,
                    "signal_tracker": tracker_result,
                }

            webhook_server = WebhookServer(
                bot=bot,
                dispatcher=dp,
                maintenance_callback=maintenance_cycle,
            )
            await webhook_server.start()
            logging.info("Liquidity Vision started in webhook mode.")
            stop_event = asyncio.Event()
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                try:
                    loop.add_signal_handler(sig, stop_event.set)
                except (NotImplementedError, RuntimeError):
                    pass
            await stop_event.wait()
        else:
            # Local development only. Ensure an old webhook cannot block
            # getUpdates, then use regular long polling.
            await bot.delete_webhook(drop_pending_updates=False)
            logging.info("Liquidity Vision started in polling mode.")
            await dp.start_polling(
                bot,
                allowed_updates=dp.resolve_used_update_types(),
                handle_signals=True,
                close_bot_session=False,
            )
    finally:
        await _stop_workers(workers, worker_tasks)
        if webhook_server is not None:
            await webhook_server.stop()
        await dp.emit_shutdown(bot=bot)
        await bot.session.close()
        logging.info("Liquidity Vision stopped cleanly.")


if __name__ == "__main__":
    asyncio.run(main())
