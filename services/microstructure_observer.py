from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid
from typing import Any

from database.database import (
    acquire_lease,
    connect,
    release_lease,
    runtime_finished,
    runtime_started,
)
from services.exchanges.bingx_swap import BingXSwapAdapter
from services.exchanges.models import ExchangeCredentials
from services.market_intelligence import BoundedMicrostructureBuffer
from services.market_intelligence_repository import MarketIntelligenceRepository


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        logging.warning("microstructure_config_invalid value=%r fallback=%s", value, default)
        parsed = default
    return max(minimum, min(parsed, maximum))


def _public_bingx_adapter() -> BingXSwapAdapter:
    """Construct a credential-empty client with no authenticated authority."""
    adapter = BingXSwapAdapter(
        ExchangeCredentials(
            api_key="",
            api_secret="",
            testnet=False,
        ),
        timeout_seconds=float(os.getenv("EXCHANGE_HTTP_TIMEOUT", "10")),
        connect_timeout_seconds=float(os.getenv("EXCHANGE_CONNECT_TIMEOUT", "5")),
        read_timeout_seconds=float(os.getenv("EXCHANGE_READ_TIMEOUT", "12")),
        max_attempts=int(os.getenv("EXCHANGE_MAX_ATTEMPTS", "3")),
        retry_backoff_seconds=float(os.getenv("EXCHANGE_RETRY_BACKOFF", "0.35")),
    )
    if adapter.configured:
        raise RuntimeError("MICROSTRUCTURE_PUBLIC_CLIENT_HAS_CREDENTIALS")
    return adapter


class MicrostructureObserver:
    """Opt-in, bounded public-data observer isolated from every execution path."""

    worker_name = "microstructure_observer"

    def __init__(self, interval_seconds: int | None = None) -> None:
        self.interval_seconds = _bounded_int(
            interval_seconds if interval_seconds is not None else os.getenv("MICROSTRUCTURE_INTERVAL_SECONDS", "60"),
            default=60, minimum=30, maximum=3600,
        )
        self.configured_value = os.getenv("MICROSTRUCTURE_COLLECTION_ENABLED")
        self.enabled = (self.configured_value or "false").strip().lower() in {
            "1", "true", "yes", "on",
        }
        self.configuration_reason = (
            "ENABLED_BY_CONFIGURATION" if self.enabled else
            "DISABLED_BY_CONFIGURATION" if self.configured_value is not None else
            "DISABLED_DEFAULT_EXPLICIT_OPT_IN_REQUIRED"
        )
        self.max_symbols = _bounded_int(os.getenv("MICROSTRUCTURE_MAX_SYMBOLS", "8"),
                                        default=8, minimum=1, maximum=20)
        self.samples_per_symbol = _bounded_int(os.getenv("MICROSTRUCTURE_SAMPLES_PER_SYMBOL", "5"),
                                               default=5, minimum=3, maximum=12)
        self.sample_spacing_ms = _bounded_int(os.getenv("MICROSTRUCTURE_SAMPLE_SPACING_MS", "400"),
                                              default=400, minimum=100, maximum=5000)
        self.max_levels = _bounded_int(os.getenv("MICROSTRUCTURE_MAX_LEVELS", "50"),
                                       default=50, minimum=5, maximum=100)
        self.buffer = BoundedMicrostructureBuffer(
            max_symbols=self.max_symbols,
            max_snapshots_per_symbol=self.samples_per_symbol,
        )
        self.repository = MarketIntelligenceRepository()
        self.owner_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self._stop = asyncio.Event()
        self._cycle_lock = asyncio.Lock()

    def stop(self) -> None:
        self._stop.set()

    def _symbols(self) -> list[str]:
        with connect() as conn:
            rows = conn.execute("""SELECT symbol,MAX(relevant_at) latest FROM (
                    SELECT symbol,updated_at relevant_at FROM signals
                    WHERE status IN ('WATCHING','TRIGGERED','ACTIVE','TP1','TP2')
                    UNION ALL
                    SELECT symbol,updated_at relevant_at FROM signal_candidates
                    WHERE status='PENDING'
                    UNION ALL
                    SELECT symbol,updated_at relevant_at FROM analysis_observations
                    WHERE promoted_signal_id IS NULL
                    UNION ALL
                    SELECT symbol,CAST(created_at AS TEXT) relevant_at FROM user_watchlist
                ) relevant_symbols
                GROUP BY symbol ORDER BY latest DESC LIMIT ?""", (self.max_symbols,)).fetchall()
        return [str(row["symbol"]).upper() for row in rows]

    def _lease_ttl(self) -> int:
        request_budget = float(os.getenv("EXCHANGE_HTTP_TIMEOUT", "10")) + 2
        spacing_budget = max(0, self.samples_per_symbol - 1) * self.sample_spacing_ms / 1000
        one_symbol_budget = (self.samples_per_symbol + 1) * request_budget + spacing_budget + 30
        return max(self.interval_seconds * 2, int(one_symbol_budget) + 1, 180)

    async def check_once(self) -> dict[str, Any]:
        if not self.enabled:
            details = {"skipped": True, "reason": self.configuration_reason,
                       "enabled": False, "configured_value": self.configured_value,
                       "symbols": 0, "persisted": 0, "errors": 0}
            runtime_finished(self.worker_name, processed=0, errors=0, details=details)
            return {"skipped": True, "reason": "DISABLED", "symbols": 0,
                    "persisted": 0, "errors": 0}
        if self._cycle_lock.locked():
            return {"skipped": True, "reason": "LOCAL_BUSY", "symbols": 0, "persisted": 0, "errors": 0}
        async with self._cycle_lock:
            return await self._check_once_locked()

    async def _check_once_locked(self) -> dict[str, Any]:
        ttl = self._lease_ttl()
        if not acquire_lease(self.worker_name, self.owner_id, ttl):
            return {"skipped": True, "reason": "LEASE_BUSY", "symbols": 0, "persisted": 0, "errors": 0}
        runtime_started(self.worker_name)
        persisted = errors = 0
        symbols = self._symbols()
        adapter: BingXSwapAdapter | None = None
        try:
            adapter = _public_bingx_adapter()
            for symbol in symbols:
                if not acquire_lease(self.worker_name, self.owner_id, ttl):
                    raise RuntimeError("MICROSTRUCTURE_OBSERVER_LEASE_LOST")
                aggregate = None
                try:
                    for sample_index in range(self.samples_per_symbol):
                        snapshot = await asyncio.wait_for(
                            adapter.market_depth(symbol, self.max_levels),
                            timeout=float(os.getenv("EXCHANGE_HTTP_TIMEOUT", "10")) + 2,
                        )
                        aggregate = self.buffer.ingest(symbol, snapshot)
                        if sample_index + 1 < self.samples_per_symbol:
                            await asyncio.sleep(self.sample_spacing_ms / 1000)
                    if aggregate is not None:
                        # Derivatives context is useful but must not make a valid
                        # public depth aggregate disappear. BingX may degrade one
                        # public endpoint independently of the other.
                        try:
                            context = await asyncio.wait_for(
                                adapter.funding_open_interest(symbol),
                                timeout=float(os.getenv("EXCHANGE_HTTP_TIMEOUT", "10")) + 2,
                            )
                            aggregate["funding_open_interest"] = {
                                **context, "status": "AVAILABLE", "freshness": "FRESH",
                            }
                        except Exception as exc:
                            aggregate["funding_open_interest"] = {
                                "status": "UNAVAILABLE", "freshness": "UNAVAILABLE",
                                "reason_code": f"{type(exc).__name__}",
                                "source": "BINGX_PUBLIC_FUTURES_MARKET",
                            }
                            logging.warning(
                                "microstructure_derivatives_stage=unavailable symbol=%s error_code=%s",
                                symbol, type(exc).__name__,
                            )
                        persisted += int(self.repository.persist_microstructure(
                            symbol=symbol,
                            exchange="bingx",
                            environment=getattr(adapter, "environment", "unknown"),
                            aggregate=aggregate,
                            ttl_seconds=max(self.interval_seconds * 2, 60),
                        ))
                    logging.info(
                        "microstructure_stage=complete symbol=%s samples=%s status=%s",
                        symbol,
                        self.samples_per_symbol,
                        (aggregate or {}).get("status", "UNAVAILABLE"),
                    )
                except Exception as exc:
                    errors += 1
                    logging.warning(
                        "microstructure_stage=failed symbol=%s error_code=%s",
                        symbol,
                        type(exc).__name__,
                        exc_info=True,
                    )
            details = {"skipped": False, "symbols": len(symbols), "persisted": persisted,
                       "errors": errors, "bounded": True, "lease_ttl_seconds": ttl}
            runtime_finished(self.worker_name, processed=len(symbols), errors=errors, details=details)
            return details
        except Exception as exc:
            runtime_finished(self.worker_name, processed=0, errors=1,
                             error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            try:
                if adapter is not None:
                    await adapter.close()
            finally:
                release_lease(self.worker_name, self.owner_id)

    async def run_forever(self) -> None:
        logging.info(
            "MicrostructureObserver started: enabled=%s reason=%s configured_value=%r interval=%ss "
            "max_symbols=%s samples=%s",
            self.enabled, self.configuration_reason, self.configured_value,
            self.interval_seconds, self.max_symbols, self.samples_per_symbol,
        )
        while not self._stop.is_set():
            try:
                await self.check_once()
            except Exception:
                logging.exception("Microstructure observer cycle failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                pass
