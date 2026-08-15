from __future__ import annotations

import asyncio
import logging
import os
import signal
import time

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from config import BOT_TOKEN
from database.database import create_tables, database_backend, persistent_database, ping_database
from handlers.admin import router as admin_router
from handlers.operator import router as operator_router
from handlers.ai_trading import router as ai_trading_router
from handlers.analyze import router as analyze_router
from handlers.copy_trading import router as copy_trading_router
from handlers.fear import router as fear_router
from handlers.exchanges import router as exchanges_router
from handlers.help import router as help_router
from handlers.journal import router as journal_router
from handlers.language import router as language_router
from handlers.intelligence_hub import router as intelligence_hub_router
from handlers.market import router as market_router
from handlers.menu import router as menu_router
from handlers.news import router as news_router
from handlers.premium import router as premium_router
from handlers.preferences import router as preferences_router
from handlers.price import router as price_router
from handlers.profile import router as profile_router
from handlers.research import router as research_router
from handlers.scanner import router as scanner_router
from handlers.start import router as start_router
from services.observation_monitor import ObservationMonitor
from services.signal_tracker import SignalTracker
from services.watch_engine import WatchEngine
from services.webhook_server import WebhookServer
from services.trade_memory import TradeMemoryService
from services.historical_execution_migration import HistoricalExecutionMigrationService
from services.copy_execution_worker import CopyExecutionWorker
from services.ai_trading import AIShadowWorker, configured_ai_interval
from services.ai_operations import AIConfigurationValidator
from services.ai_intelligence import AIObservationIntelligence
from services.research_worker import ResearchWorker
from services.microstructure_observer import MicrostructureObserver
from services.live_reconciliation_worker import LiveReconciliationWorker
from services.live_copy import LiveCopyWorker
from services.command_catalog import MAIN_MENU_COMMANDS
from services.localization import LocalizationService, SUPPORTED_LANGUAGES
from services.operational_retention import OperationalRetentionService
from services.product_analytics_middleware import ProductAnalyticsMiddleware

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(message)s",
)


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.message.outer_middleware(ProductAnalyticsMiddleware())
    dp.include_router(operator_router)
    dp.include_router(admin_router)
    dp.include_router(ai_trading_router)
    dp.include_router(start_router)
    dp.include_router(language_router)
    dp.include_router(help_router)
    dp.include_router(price_router)
    dp.include_router(analyze_router)
    dp.include_router(copy_trading_router)
    dp.include_router(research_router)
    dp.include_router(exchanges_router)
    dp.include_router(profile_router)
    dp.include_router(scanner_router)
    dp.include_router(fear_router)
    dp.include_router(market_router)
    dp.include_router(news_router)
    dp.include_router(journal_router)
    dp.include_router(intelligence_hub_router)
    dp.include_router(premium_router)
    dp.include_router(preferences_router)
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
    startup_started = time.perf_counter()
    logging.info("Creating database...")
    phase = time.perf_counter()
    create_tables()
    logging.info("Startup phase database_schema duration_ms=%.1f", (time.perf_counter() - phase) * 1000)
    phase = time.perf_counter()
    migration = HistoricalExecutionMigrationService().run(
        batch_size=int(os.getenv("HISTORICAL_MIGRATION_BATCH_SIZE", "500"))
    )
    logging.info("Historical execution migration: %s", migration.as_dict())
    logging.info("Startup phase historical_migration duration_ms=%.1f", (time.perf_counter() - phase) * 1000)
    phase = time.perf_counter()
    backfill = TradeMemoryService().backfill(limit=int(os.getenv("MEMORY_BACKFILL_LIMIT", "500")))
    logging.info("AI memory backfill: scanned=%s created=%s", backfill["scanned"], backfill["created"])
    logging.info("Startup phase memory_backfill duration_ms=%.1f", (time.perf_counter() - phase) * 1000)
    phase = time.perf_counter()
    retention = OperationalRetentionService().run()
    logging.info("Operational retention: %s duration_ms=%.1f", retention, (time.perf_counter() - phase) * 1000)
    db_health = ping_database()
    logging.info("Database ready: backend=%s persistent=%s latency_ms=%s", database_backend(), persistent_database(), db_health.get("latency_ms"))
    ai_config = AIConfigurationValidator().validate()
    ai_startup = AIObservationIntelligence.startup_validate()
    if not ai_startup["valid"]:
        logging.error("AI observation startup validation failed: %s", ai_startup)
    else:
        logging.info("AI observation startup validation: %s", ai_startup)
    if not ai_config.valid:
        logging.error("AI provider activation blocked: errors=%s warnings=%s", ai_config.errors, ai_config.warnings)
    else:
        logging.info("AI provider configuration valid: warnings=%s; certification is still required", ai_config.warnings)

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    set_commands = getattr(bot, "set_my_commands", None)
    if callable(set_commands):
        await set_commands([BotCommand(command=name, description=description)
                            for name, description in MAIN_MENU_COMMANDS])
        i18n = LocalizationService()
        for language_code in SUPPORTED_LANGUAGES:
            commands = [BotCommand(command=name,
                                   description=i18n.t(f"menu.{name}", language=language_code))
                        for name, _ in MAIN_MENU_COMMANDS]
            await set_commands(commands, language_code=language_code)
    dp = build_dispatcher()
    logging.info("Startup initialization complete duration_ms=%.1f", (time.perf_counter() - startup_started) * 1000)

    tracker = SignalTracker(interval_seconds=int(os.getenv("SIGNAL_CHECK_INTERVAL", "60")), bot=bot)
    observation_monitor = ObservationMonitor(bot=bot)
    watch_engine = WatchEngine(bot=bot)
    copy_execution = CopyExecutionWorker()
    ai_shadow = AIShadowWorker(interval_seconds=configured_ai_interval())
    research = ResearchWorker()
    microstructure = MicrostructureObserver(bot=bot)
    live_reconciliation = LiveReconciliationWorker(bot=bot)
    live_copy = LiveCopyWorker(adapter_factory=LiveReconciliationWorker._adapter, bot=bot)
    workers = [tracker, observation_monitor, watch_engine, copy_execution, ai_shadow, research,
               microstructure, live_reconciliation, live_copy]
    worker_tasks = [
        asyncio.create_task(tracker.run_forever(), name="signal-tracker"),
        asyncio.create_task(observation_monitor.run_forever(), name="observation-monitor"),
        asyncio.create_task(watch_engine.run_forever(), name="watch-engine"),
        asyncio.create_task(copy_execution.run_forever(), name="copy-execution"),
        asyncio.create_task(ai_shadow.run_forever(), name="ai-shadow"),
        asyncio.create_task(research.run_forever(), name="research-engine"),
        asyncio.create_task(microstructure.run_forever(), name="microstructure-observer"),
        asyncio.create_task(live_reconciliation.run_forever(), name="live-reconciliation"),
        asyncio.create_task(live_copy.run_forever(), name="live-copy-dispatcher"),
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
                copy_result = await copy_execution.check_once()
                ai_result = await ai_shadow.check_once()
                research_result = await research.check_once()
                microstructure_result = await microstructure.check_once()
                live_reconciliation_result = await live_reconciliation.check_once()
                live_copy_result = await live_copy.check_once()
                return {
                    "database_backend": database_backend(),
                    "persistent_database": persistent_database(),
                    "watch_engine": watch_result,
                    "observation_monitor": observation_result,
                    "signal_tracker": tracker_result,
                    "copy_execution": copy_result,
                    "ai_observation": ai_result,
                    "research_engine": research_result,
                    "microstructure_observer": microstructure_result,
                    "live_reconciliation": live_reconciliation_result,
                    "live_copy_dispatcher": live_copy_result,
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
