"""Singleton Render worker for persistent Telegram product operations."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import socket
from typing import Any

from database.database import acquire_lease, release_lease
from database.schema_management import initialize_service_database
from services.operational_runtime import (
    OPERATIONAL_COMPONENTS, OPERATIONAL_WORKER_NAME, OperationalHealthRepository,
    OperationalMaintenanceWorker, child_runtime_states, utc_now,
)


LEASE_NAME = "operational-product-production-v1"


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Lease-protected PAPER/product operations")
    value.add_argument("--print-capabilities", action="store_true")
    return value


def capabilities() -> dict[str, Any]:
    return {
        "mode": "PAPER_PRODUCT_OPERATIONS",
        "components": list(OPERATIONAL_COMPONENTS),
        "telegram_polling": False,
        "telegram_webhook": False,
        "http_server": False,
        "forward_raw_collection": False,
        "live_execution_authority": False,
        "economic_authority": "PAPER_ONLY",
    }


def build_workers(bot: Any) -> dict[str, Any]:
    """Import product workers only inside the dedicated process."""
    from services.ai_trading import AIShadowWorker, configured_ai_interval
    from services.copy_execution_worker import CopyExecutionWorker
    from services.observation_monitor import ObservationMonitor
    from services.pump_dump_monitor import PumpDumpMonitor
    from services.research_worker import ResearchWorker
    from services.signal_tracker import SignalTracker
    from services.watch_engine import WatchEngine

    return {
        "signal_tracker": SignalTracker(
            interval_seconds=int(os.getenv("SIGNAL_CHECK_INTERVAL", "60")), bot=bot,
        ),
        "observation_monitor": ObservationMonitor(bot=bot),
        "watch_engine": WatchEngine(bot=bot),
        "copy_execution": CopyExecutionWorker(),
        "ai_shadow": AIShadowWorker(interval_seconds=configured_ai_interval()),
        "research_engine": ResearchWorker(),
        "pump_dump_monitor": PumpDumpMonitor(bot=bot),
        "operational_maintenance": OperationalMaintenanceWorker(),
    }


async def run() -> dict[str, Any]:
    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode
    from config import BOT_TOKEN

    initialize_service_database(service_name="operational-worker")
    instance_id = (
        os.getenv("RENDER_INSTANCE_ID") or os.getenv("RENDER_SERVICE_ID") or
        f"{socket.gethostname()}-{os.getpid()}"
    )
    lease_seconds = max(60, int(os.getenv("OPERATIONAL_LEASE_SECONDS", "120")))
    if not acquire_lease(LEASE_NAME, instance_id, lease_seconds):
        raise RuntimeError("authoritative operational worker lease is already held")

    started_at = utc_now()
    health = OperationalHealthRepository()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    workers = build_workers(bot)
    tasks: dict[str, asyncio.Task[Any]] = {}
    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(signum, shutdown.set)
        except (NotImplementedError, RuntimeError):
            pass

    async def heartbeat() -> None:
        while True:
            if not acquire_lease(LEASE_NAME, instance_id, lease_seconds):
                raise RuntimeError("authoritative operational worker lease was lost")
            health.heartbeat(
                instance_id=instance_id, state="RUNNING", started_at=started_at,
                child_states=child_runtime_states(),
            )
            await asyncio.sleep(max(10, lease_seconds // 3))

    # Small deterministic offsets prevent every network/dataframe worker from
    # allocating its peak buffers in the same startup second.
    async def start_component(name: str, worker: Any, delay: int) -> None:
        if delay:
            await asyncio.sleep(delay)
        await worker.run_forever()

    try:
        for offset, (name, worker) in enumerate(workers.items()):
            tasks[name] = asyncio.create_task(
                start_component(name, worker, offset * 3), name=f"operational:{name}",
            )
        tasks["heartbeat"] = asyncio.create_task(heartbeat(), name="operational:heartbeat")
        tasks["shutdown"] = asyncio.create_task(shutdown.wait(), name="operational:shutdown")
        done, _ = await asyncio.wait(tasks.values(), return_when=asyncio.FIRST_COMPLETED)
        if tasks["shutdown"] in done and shutdown.is_set():
            health.heartbeat(
                instance_id=instance_id, state="STOPPING", started_at=started_at,
                child_states=child_runtime_states(),
            )
            return {"status": "stopped", **capabilities()}
        failed = next((task for task in done if task is not tasks["shutdown"]), None)
        if failed is not None:
            exception = failed.exception()
            raise exception or RuntimeError(f"operational component exited: {failed.get_name()}")
        return {"status": "stopped", **capabilities()}
    except BaseException as exc:
        try:
            health.heartbeat(
                instance_id=instance_id, state="FAILED", started_at=started_at,
                child_states=child_runtime_states(), last_error=str(exc)[:1000],
            )
        except Exception:
            pass
        raise
    finally:
        for worker in workers.values():
            stop = getattr(worker, "stop", None)
            if callable(stop):
                stop()
        for task in tasks.values():
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks.values(), return_exceptions=True)
        await bot.session.close()
        release_lease(LEASE_NAME, instance_id)


def main() -> int:
    args = parser().parse_args()
    if args.print_capabilities:
        print(json.dumps(capabilities(), indent=2, sort_keys=True))
        return 0
    result = asyncio.run(run())
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
