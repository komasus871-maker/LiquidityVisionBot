"""Small, dependency-free supervision primitives for production workers."""
from __future__ import annotations

import asyncio
import logging
import random
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProviderRegionBlockedError(RuntimeError):
    """A public provider has rejected this deployment region."""

    def __init__(self, provider: str, status: int = 451) -> None:
        self.provider = provider.upper()
        self.status = int(status)
        super().__init__(f"PROVIDER_REGION_BLOCKED:{self.provider}:HTTP_{self.status}")


async def wait_for_authoritative_lease(
    acquire: Callable[[str, str, int], bool], lease_name: str, owner_id: str,
    ttl_seconds: int, *, shutdown: asyncio.Event,
    base_backoff_seconds: float = 1.0, max_backoff_seconds: float = 30.0,
) -> bool:
    """Wait for a live owner to release a singleton lease; expired rows are reclaimed atomically."""
    attempts = 0
    while not shutdown.is_set():
        acquire_error: str | None = None
        try:
            acquired = await bounded_thread_call(
                acquire, lease_name, owner_id, ttl_seconds,
                timeout_seconds=min(max(10.0, float(ttl_seconds)), 30.0),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            acquired = False
            acquire_error = type(exc).__name__
        if acquired:
            return True
        attempts += 1
        ceiling = min(
            max_backoff_seconds,
            max(0.05, base_backoff_seconds) * (2 ** min(attempts - 1, 5)),
        )
        delay = ceiling * random.uniform(0.8, 1.2)
        logging.warning(
            "authoritative_lease_wait lease=%s attempt=%s retry_seconds=%.2f error_type=%s",
            lease_name, attempts, delay, acquire_error,
        )
        try:
            await asyncio.wait_for(shutdown.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass
    return False


class RestartingTaskSupervisor:
    """Run a long-lived component, restarting exits, failures, and stalls."""

    def __init__(
        self, name: str, factory: Callable[[], Any], *,
        shutdown: asyncio.Event, hang_timeout_seconds: float | None = None,
        base_backoff_seconds: float = 1.0, max_backoff_seconds: float = 30.0,
    ) -> None:
        self.name = name
        self.factory = factory
        self.shutdown = shutdown
        self.hang_timeout_seconds = hang_timeout_seconds
        self.base_backoff_seconds = max(0.01, base_backoff_seconds)
        self.max_backoff_seconds = max(self.base_backoff_seconds, max_backoff_seconds)
        self.task_state = "NOT_STARTED"
        self.current_stage = "not_started"
        self.last_progress_at: str | None = None
        self.last_success_at: str | None = None
        self.last_error: str | None = None
        self.restart_count = 0
        self.last_restart_reason: str | None = None
        self._worker: Any = None

    def snapshot(self) -> dict[str, Any]:
        worker = self._worker
        return {
            "task_state": self.task_state,
            "current_stage": getattr(worker, "current_stage", self.current_stage),
            "last_progress_at": getattr(worker, "last_progress_at", self.last_progress_at),
            "last_success_at": getattr(worker, "last_success_at", self.last_success_at),
            "last_error": getattr(worker, "last_error", None) or self.last_error,
            "restart_count": self.restart_count,
            "last_restart_reason": self.last_restart_reason,
        }

    async def _backoff(self, failures: int) -> None:
        ceiling = min(self.max_backoff_seconds, self.base_backoff_seconds * (2 ** min(failures, 5)))
        delay = ceiling * random.uniform(0.8, 1.2)
        try:
            await asyncio.wait_for(self.shutdown.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass

    async def run(self) -> None:
        failures = 0
        while not self.shutdown.is_set():
            self._worker = self.factory()
            self.task_state = "STARTING"
            self.current_stage = "starting"
            self.last_progress_at = utc_now()
            task = asyncio.create_task(
                self._worker.run_forever(), name=f"supervised:{self.name}",
            )
            self.task_state = "RUNNING"
            self.current_stage = "running"
            reason: str | None = None
            try:
                while not self.shutdown.is_set():
                    done, _ = await asyncio.wait({task}, timeout=1.0)
                    if done:
                        exception = task.exception()
                        reason = (
                            f"{type(exception).__name__}: {exception}" if exception
                            else "TASK_EXITED"
                        )
                        break
                    progress = getattr(self._worker, "last_progress_monotonic", None)
                    if (
                        self.hang_timeout_seconds is not None and progress is not None
                        and time.monotonic() - float(progress) > self.hang_timeout_seconds
                    ):
                        reason = f"WATCHDOG_TIMEOUT:{self.hang_timeout_seconds:g}s"
                        self.task_state = "CANCELLING_HUNG_TASK"
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                        break
                if self.shutdown.is_set():
                    self.task_state = "STOPPING"
                    stop = getattr(self._worker, "stop", None)
                    if callable(stop):
                        stop()
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    return
            except asyncio.CancelledError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise

            failures += 1
            self.restart_count += 1
            self.last_restart_reason = reason or "UNKNOWN_EXIT"
            self.last_error = self.last_restart_reason
            self.task_state = "RESTART_BACKOFF"
            stop = getattr(self._worker, "stop", None)
            if callable(stop):
                stop()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await self._backoff(failures)

        self.task_state = "STOPPED"


async def bounded_thread_call(
    function: Callable[..., Any], *args: Any, timeout_seconds: float = 20.0,
    **kwargs: Any,
) -> Any:
    """Keep synchronous I/O off the event loop and bound the caller's wait."""
    return await asyncio.wait_for(
        asyncio.to_thread(function, *args, **kwargs), timeout=max(0.01, timeout_seconds),
    )


class PeriodicHeartbeatThread:
    """Publish process liveness independently of an application's asyncio loop."""

    def __init__(
        self, callback: Callable[[], None], *, interval_seconds: float,
        fatal_exceptions: tuple[type[BaseException], ...] = (),
        on_fatal: Callable[[BaseException], None] | None = None,
        name: str = "runtime-heartbeat",
    ) -> None:
        self.callback = callback
        self.interval_seconds = max(0.01, interval_seconds)
        self.fatal_exceptions = fatal_exceptions
        self.on_fatal = on_fatal
        self.name = name
        self.last_error: str | None = None
        self.last_success_at: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.callback()
                self.last_success_at = utc_now()
                self.last_error = None
            except self.fatal_exceptions as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"[:1000]
                if self.on_fatal is not None:
                    self.on_fatal(exc)
                return
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"[:1000]
            self._stop.wait(self.interval_seconds)

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("heartbeat thread already started")
        self._thread = threading.Thread(target=self._run, name=self.name, daemon=True)
        self._thread.start()

    def stop(self, timeout_seconds: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(0.0, timeout_seconds))
