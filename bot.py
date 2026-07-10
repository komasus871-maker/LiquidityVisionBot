import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from config import BOT_TOKEN

from handlers.start import router as start_router
from handlers.help import router as help_router
from handlers.price import router as price_router
from handlers.analyze import router as analyze_router
from handlers.profile import router as profile_router
from handlers.scanner import router as scanner_router
from handlers.menu import router as menu_router
from handlers.fear import router as fear_router
from handlers.market import router as market_router
from handlers.news import router as news_router
from handlers.journal import router as journal_router
from handlers.premium import router as premium_router

from database.database import create_tables
from services.signal_tracker import SignalTracker
from services.observation_monitor import ObservationMonitor
from services.health_server import start_health_server


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)


async def main():

    health_runner = await start_health_server()

    logging.info("Creating database...")
    create_tables()
    logging.info("Database ready.")

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML
        )
    )

    dp = Dispatcher()

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

    logging.info("Liquidity Vision started.")

    tracker = SignalTracker(interval_seconds=60, bot=bot)
    tracker_task = asyncio.create_task(tracker.run_forever())
    observation_monitor = ObservationMonitor(bot=bot)
    observation_task = asyncio.create_task(observation_monitor.run_forever())

    await bot.delete_webhook(
        drop_pending_updates=True
    )

    try:
        await dp.start_polling(bot)
    finally:
        # Stop background workers first so they cannot use the bot session
        # while it is being closed.
        if hasattr(tracker, "stop"):
            tracker.stop()
        if hasattr(observation_monitor, "stop"):
            observation_monitor.stop()

        tracker_task.cancel()
        observation_task.cancel()
        await asyncio.gather(
            tracker_task,
            observation_task,
            return_exceptions=True,
        )

        await bot.session.close()
        await health_runner.cleanup()
        logging.info("Liquidity Vision stopped cleanly.")


if __name__ == "__main__":

    asyncio.run(
        main()
    )