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
from services.runtime_supervision import (
    PeriodicHeartbeatThread, RestartingTaskSupervisor, bounded_thread_call,
    wait_for_authoritative_lease,
)


LEASE_NAME = "operational-product-production-v1"


class LeaseLostError(RuntimeError):
    pass


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


def build_worker_factories(bot: Any) -> dict[str, Any]:
    """Import product workers only inside the dedicated process."""
    from services.ai_trading import AIShadowWorker, configured_ai_interval
    from services.copy_execution_worker import CopyExecutionWorker
    from services.observation_monitor import ObservationMonitor
    from services.pump_dump_monitor import PumpDumpMonitor
    from services.research_worker import ResearchWorker
    from services.signal_tracker import SignalTracker
    from services.watch_engine import WatchEngine

    return {
        "signal_tracker": lambda: SignalTracker(
            interval_seconds=int(os.getenv("SIGNAL_CHECK_INTERVAL", "60")), bot=bot,
        ),
        "observation_monitor": lambda: ObservationMonitor(bot=bot),
        "watch_engine": lambda: WatchEngine(bot=bot),
        "copy_execution": CopyExecutionWorker,
        "ai_shadow": lambda: AIShadowWorker(interval_seconds=configured_ai_interval()),
        "research_engine": ResearchWorker,
        "pump_dump_monitor": lambda: PumpDumpMonitor(bot=bot),
        "operational_maintenance": OperationalMaintenanceWorker,
    }


def build_workers(bot: Any) -> dict[str, Any]:
    """Compatibility helper for capability tests and local inspection."""
    return {name: factory() for name, factory in build_worker_factories(bot).items()}


async def run() -> dict[str, Any]:
    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode
    from config import BOT_TOKEN

    initialize_service_database(service_name="operational-worker")
    process_identity = os.getenv("RENDER_INSTANCE_ID") or os.getenv("RENDER_SERVICE_ID") or "local"
    instance_id = f"{process_identity}:{socket.gethostname()}:{os.getpid()}"
    lease_seconds = max(60, int(os.getenv("OPERATIONAL_LEASE_SECONDS", "120")))
    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(signum, shutdown.set)
        except (NotImplementedError, RuntimeError):
            pass
    acquired = await wait_for_authoritative_lease(
        acquire_lease, LEASE_NAME, instance_id, lease_seconds, shutdown=shutdown,
        base_backoff_seconds=float(os.getenv("OPERATIONAL_LEASE_WAIT_BASE_SECONDS", "1")),
        max_backoff_seconds=float(os.getenv("OPERATIONAL_LEASE_WAIT_MAX_SECONDS", "30")),
    )
    if not acquired:
        return {"status": "stopped_before_lease", **capabilities()}

    started_at = utc_now()
    health = OperationalHealthRepository()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    factories = build_worker_factories(bot)
    tasks: dict[str, asyncio.Task[Any]] = {}
    supervisors = {
        name: RestartingTaskSupervisor(
            name, factory, shutdown=shutdown,
            hang_timeout_seconds=(
                float(os.getenv("PUMP_SCANNER_TASK_WATCHDOG_SECONDS", "180"))
                if name == "pump_dump_monitor" else None
            ),
        )
        for name, factory in factories.items()
    }

    def supervisor_states() -> dict[str, Any]:
        return {name: supervisor.snapshot() for name, supervisor in supervisors.items()}

    async def publish_heartbeat(state: str = "RUNNING", last_error: str | None = None) -> None:
        states = await bounded_thread_call(
            child_runtime_states, supervisor_states(), timeout_seconds=20,
        )
        await bounded_thread_call(
            health.heartbeat, instance_id=instance_id, state=state,
            started_at=started_at, child_states=states, last_error=last_error,
            timeout_seconds=20,
        )

    heartbeat_failed = asyncio.Event()
    heartbeat_failure: list[BaseException] = []

    def heartbeat_fatal(exc: BaseException) -> None:
        heartbeat_failure.append(exc)
        loop.call_soon_threadsafe(heartbeat_failed.set)

    def heartbeat_tick() -> None:
        if not acquire_lease(LEASE_NAME, instance_id, lease_seconds):
            raise LeaseLostError("authoritative operational worker lease was lost")
        states = child_runtime_states(supervisor_states())
        degraded = any(
            value.get("task_state") not in {"RUNNING", "STARTING"}
            or bool(value.get("last_error"))
            for value in supervisor_states().values()
        )
        publication_error = heartbeat_thread.last_error
        health.heartbeat(
            instance_id=instance_id,
            state="DEGRADED" if degraded or publication_error else "RUNNING",
            started_at=started_at, child_states=states,
            last_error=publication_error,
        )

    heartbeat_thread = PeriodicHeartbeatThread(
        heartbeat_tick,
        interval_seconds=max(15, min(30, int(os.getenv("OPERATIONAL_HEARTBEAT_SECONDS", "20")))),
        fatal_exceptions=(LeaseLostError,), on_fatal=heartbeat_fatal,
        name="operational-heartbeat",
    )

    try:
        heartbeat_thread.start()
        for name, supervisor in supervisors.items():
            tasks[name] = asyncio.create_task(supervisor.run(), name=f"operational:{name}")
        tasks["shutdown"] = asyncio.create_task(shutdown.wait(), name="operational:shutdown")
        tasks["heartbeat_failed"] = asyncio.create_task(
            heartbeat_failed.wait(), name="operational:heartbeat-failed",
        )
        done, _ = await asyncio.wait(
            {tasks["heartbeat_failed"], tasks["shutdown"]},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if tasks["shutdown"] in done and shutdown.is_set():
            await publish_heartbeat("STOPPING")
            return {"status": "stopped", **capabilities()}
        raise heartbeat_failure[-1] if heartbeat_failure else RuntimeError(
            "operational heartbeat thread exited"
        )
    except BaseException as exc:
        try:
            await publish_heartbeat("FAILED", str(exc)[:1000])
        except Exception:
            pass
        raise
    finally:
        shutdown.set()
        heartbeat_thread.stop(timeout_seconds=20)
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
