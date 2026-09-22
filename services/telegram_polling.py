"""Lease-protected Telegram long polling for the production web process."""
from __future__ import annotations

import asyncio
import logging
import os
import socket
from collections.abc import Callable
from typing import Any

from database.database import acquire_lease, release_lease
from services.runtime_supervision import bounded_thread_call, wait_for_authoritative_lease


TELEGRAM_POLLER_LEASE = "telegram-poller-production-v1"


def polling_owner_id() -> str:
    process_identity = os.getenv("RENDER_INSTANCE_ID") or os.getenv("RENDER_SERVICE_ID") or "local"
    return f"{process_identity}:{socket.gethostname()}:{os.getpid()}"


class SingletonTelegramPoller:
    """Run at most one production poller and retain the lease until polling stops."""

    def __init__(
        self,
        bot: Any,
        dispatcher: Any,
        *,
        acquire: Callable[[str, str, int], bool] = acquire_lease,
        release: Callable[[str, str], None] = release_lease,
        owner_id: str | None = None,
        lease_seconds: int | None = None,
        wait_base_seconds: float | None = None,
        wait_max_seconds: float | None = None,
    ) -> None:
        self.bot = bot
        self.dispatcher = dispatcher
        self.acquire = acquire
        self.release = release
        self.owner_id = owner_id or polling_owner_id()
        self.lease_seconds = max(
            120, lease_seconds or int(os.getenv("TELEGRAM_POLLING_LEASE_SECONDS", "120")),
        )
        self.wait_base_seconds = max(
            0.05,
            wait_base_seconds
            if wait_base_seconds is not None
            else float(os.getenv("TELEGRAM_POLLING_LEASE_WAIT_BASE_SECONDS", "1")),
        )
        self.wait_max_seconds = max(
            self.wait_base_seconds,
            wait_max_seconds
            if wait_max_seconds is not None
            else float(os.getenv("TELEGRAM_POLLING_LEASE_WAIT_MAX_SECONDS", "30")),
        )

    async def _renew_until_stopped(self, shutdown: asyncio.Event) -> bool:
        # Renew early enough that a bounded DB timeout plus bounded polling
        # shutdown completes well before another process can reclaim the row.
        interval = max(15.0, min(30.0, self.lease_seconds / 4))
        while not shutdown.is_set():
            try:
                await asyncio.wait_for(shutdown.wait(), timeout=interval)
                return True
            except asyncio.TimeoutError:
                pass
            try:
                renewed = await bounded_thread_call(
                    self.acquire,
                    TELEGRAM_POLLER_LEASE,
                    self.owner_id,
                    self.lease_seconds,
                    timeout_seconds=10,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                renewed = False
                logging.error(
                    "TELEGRAM_POLLING_LEASE_RENEWAL_FAILED error_type=%s owner=%s",
                    type(exc).__name__, self.owner_id,
                )
            if not renewed:
                logging.error("TELEGRAM_POLLING_LEASE_LOST owner=%s", self.owner_id)
                return False
        return True

    async def _stop_polling(self, polling_task: asyncio.Task[Any]) -> None:
        if polling_task.done():
            await asyncio.gather(polling_task, return_exceptions=True)
            return
        stop_polling = getattr(self.dispatcher, "stop_polling", None)
        if callable(stop_polling):
            try:
                await asyncio.wait_for(stop_polling(), timeout=20)
            except (RuntimeError, asyncio.TimeoutError):
                # The task may have been created but not yet acquired aiogram's
                # running lock, or a handler may exceed the shutdown window.
                # Cancellation below is safe after either bounded condition.
                pass
        if not polling_task.done():
            polling_task.cancel()
        await asyncio.gather(polling_task, return_exceptions=True)

    async def _release(self) -> None:
        try:
            await bounded_thread_call(
                self.release,
                TELEGRAM_POLLER_LEASE,
                self.owner_id,
                timeout_seconds=20,
            )
            logging.info("TELEGRAM_POLLING_LEASE_RELEASED owner=%s", self.owner_id)
        except Exception as exc:
            # Failure is safe: the owner-scoped lease expires and can then be
            # reclaimed. Never permit a replacement before polling has stopped.
            logging.error(
                "TELEGRAM_POLLING_LEASE_RELEASE_FAILED error_type=%s owner=%s",
                type(exc).__name__, self.owner_id,
            )

    async def _run_as_owner(self, shutdown: asyncio.Event) -> bool:
        deleted = await self.bot.delete_webhook(drop_pending_updates=False)
        if deleted is not True:
            raise RuntimeError("Telegram deleteWebhook did not confirm polling transition")
        logging.info(
            "TELEGRAM_POLLING_START owner=%s drop_pending_updates=false lease_seconds=%s",
            self.owner_id, self.lease_seconds,
        )
        polling_task = asyncio.create_task(
            self.dispatcher.start_polling(
                self.bot,
                allowed_updates=self.dispatcher.resolve_used_update_types(),
                handle_signals=False,
                close_bot_session=False,
            ),
            name="telegram-long-polling",
        )
        renewal_task = asyncio.create_task(
            self._renew_until_stopped(shutdown), name="telegram-polling-lease-renewal",
        )
        shutdown_task = asyncio.create_task(
            shutdown.wait(), name="telegram-polling-shutdown",
        )
        try:
            done, _ = await asyncio.wait(
                {polling_task, renewal_task, shutdown_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if polling_task in done:
                exception = None if polling_task.cancelled() else polling_task.exception()
                if polling_task.cancelled():
                    logging.warning("TELEGRAM_POLLING_CANCELLED owner=%s", self.owner_id)
                elif exception is not None:
                    logging.error(
                        "TELEGRAM_POLLING_EXITED error_type=%s owner=%s",
                        type(exception).__name__, self.owner_id,
                    )
                else:
                    logging.warning("TELEGRAM_POLLING_EXITED owner=%s", self.owner_id)
            await self._stop_polling(polling_task)
            return not shutdown.is_set()
        finally:
            for task in (renewal_task, shutdown_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(renewal_task, shutdown_task, return_exceptions=True)

    async def run(self, shutdown: asyncio.Event) -> None:
        while not shutdown.is_set():
            acquired = await wait_for_authoritative_lease(
                self.acquire,
                TELEGRAM_POLLER_LEASE,
                self.owner_id,
                self.lease_seconds,
                shutdown=shutdown,
                base_backoff_seconds=self.wait_base_seconds,
                max_backoff_seconds=self.wait_max_seconds,
            )
            if not acquired:
                return
            logging.info("TELEGRAM_POLLING_LEASE_ACQUIRED owner=%s", self.owner_id)
            retry = False
            try:
                retry = await self._run_as_owner(shutdown)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                retry = not shutdown.is_set()
                logging.error(
                    "TELEGRAM_POLLING_START_FAILED error_type=%s owner=%s",
                    type(exc).__name__, self.owner_id,
                )
            finally:
                await self._release()
            if not retry or shutdown.is_set():
                return
            try:
                await asyncio.wait_for(shutdown.wait(), timeout=self.wait_base_seconds)
            except asyncio.TimeoutError:
                pass
