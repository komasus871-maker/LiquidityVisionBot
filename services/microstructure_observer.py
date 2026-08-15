from __future__ import annotations

import asyncio
import logging
import os
import re
import socket
import uuid
from datetime import datetime, timezone
from html import escape
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
from services.intelligence_alerts import IntelligenceAlertService


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

    def __init__(self, interval_seconds: int | None = None, *, bot=None) -> None:
        self.bot = bot
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
        configured_symbols = os.getenv("MICROSTRUCTURE_SYMBOLS", "BTCUSDT")
        self.seed_symbols = tuple(dict.fromkeys(
            token for token in (re.sub(r"[^A-Z0-9]", "", item.upper())
                                for item in configured_symbols.split(",")) if token
        )) or ("BTCUSDT",)
        self.buffer = BoundedMicrostructureBuffer(
            max_symbols=self.max_symbols,
            max_snapshots_per_symbol=self.samples_per_symbol,
        )
        self.repository = MarketIntelligenceRepository()
        self.owner_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self._stop = asyncio.Event()
        self._cycle_lock = asyncio.Lock()
        self.repository.update_worker_health(
            worker_name=self.worker_name, configured_value=self.configured_value,
            configured_enabled=self.enabled, effective_enabled=self.enabled,
            state="WAITING_FOR_FIRST_SAMPLE" if self.enabled else "DISABLED_BY_CONFIGURATION",
            active_symbols=list(self.seed_symbols)[:self.max_symbols],
        )

    async def _deliver_provider_alerts(self, symbols: list[str], source_health: dict[str, dict[str, int]]) -> None:
        if self.bot is None or not symbols:
            return
        placeholders = ",".join("?" for _ in symbols)
        with connect() as conn:
            rows = conn.execute(f"""SELECT DISTINCT telegram_id,symbol,timeframe FROM user_watchlist
                WHERE symbol IN ({placeholders}) ORDER BY telegram_id,symbol LIMIT 500""", symbols).fetchall()
        alerts = IntelligenceAlertService(
            debounce_minutes=int(os.getenv("ALERT_DEBOUNCE_MINUTES", "30")))
        failed_sources = [name for name, health in source_health.items() if health.get("failed")]
        for row in rows:
            for source in failed_sources:
                decision = alerts.evaluate(
                    int(row["telegram_id"]), symbol=str(row["symbol"]), timeframe=str(row["timeframe"]),
                    alert_type="PROVIDER_DEGRADATION", state_identity=f"{source}:DEGRADED",
                    severity="WARNING", details={"source": source, "provider": "BINGX_PUBLIC_FUTURES"})
                if decision["status"] != "ELIGIBLE":
                    continue
                try:
                    await self.bot.send_message(
                        int(row["telegram_id"]),
                        f"⚠️ <b>Market-data degradation</b>\n\n"
                        f"{escape(str(row['symbol']))} · <code>{escape(source)}</code> is temporarily degraded. "
                        "Available independent sources remain usable; missing evidence is shown as unavailable.",
                        parse_mode="HTML")
                except Exception:
                    alerts.mark_delivery_failed(decision["alert_key"])
                    logging.exception("provider_degradation_alert_failed source=%s", source)
                else:
                    alerts.mark_delivered(decision["alert_key"])
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
        # A configured seed universe makes collection operational before any
        # user creates a signal/watchlist row. Previously an empty database
        # produced a successful zero-request cycle forever.
        dynamic = [str(row["symbol"]).upper().replace("-", "") for row in rows]
        return list(dict.fromkeys((*self.seed_symbols, *dynamic)))[:self.max_symbols]

    @staticmethod
    def _error_code(source: str, exc: Exception) -> str:
        explicit = str(getattr(exc, "code", "") or "").strip().upper()
        message = re.sub(r"[^A-Z0-9_]+", "_", str(exc).strip().upper()).strip("_")
        detail = explicit or (message if message and len(message) <= 48 else type(exc).__name__.upper())
        return f"BINGX_{source}_{detail}"[:80]

    async def _derivative_requests(self, adapter: BingXSwapAdapter, symbol: str) -> dict[str, Any]:
        """Collect funding and OI independently when the adapter supports V2 methods."""
        timeout = float(os.getenv("EXCHANGE_HTTP_TIMEOUT", "10")) + 2
        if hasattr(adapter, "funding_snapshot") and hasattr(adapter, "open_interest_snapshot"):
            funding, open_interest = await asyncio.gather(
                asyncio.wait_for(adapter.funding_snapshot(symbol), timeout=timeout),
                asyncio.wait_for(adapter.open_interest_snapshot(symbol), timeout=timeout),
                return_exceptions=True,
            )
            return {"FUNDING": funding, "OPEN_INTEREST": open_interest}
        # Compatibility for test/dry adapters written before the independent
        # source contract. The production BingX adapter never uses this path.
        try:
            combined = await asyncio.wait_for(adapter.funding_open_interest(symbol), timeout=timeout)
            return {"FUNDING": combined, "OPEN_INTEREST": combined}
        except Exception as exc:
            return {"FUNDING": exc, "OPEN_INTEREST": exc}

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
            self.repository.update_worker_health(
                worker_name=self.worker_name, configured_value=self.configured_value,
                configured_enabled=False, effective_enabled=False,
                state="DISABLED_BY_CONFIGURATION", heartbeat_at=datetime.now(timezone.utc).isoformat(),
                active_symbols=list(self.seed_symbols)[:self.max_symbols], lease_state="NOT_ACQUIRED",
            )
            return {"skipped": True, "reason": "DISABLED", "symbols": 0,
                    "persisted": 0, "errors": 0}
        if self._cycle_lock.locked():
            return {"skipped": True, "reason": "LOCAL_BUSY", "symbols": 0, "persisted": 0, "errors": 0}
        async with self._cycle_lock:
            return await self._check_once_locked()

    async def _check_once_locked(self) -> dict[str, Any]:
        cycle_started = datetime.now(timezone.utc)
        ttl = self._lease_ttl()
        if not acquire_lease(self.worker_name, self.owner_id, ttl):
            self.repository.update_worker_health(
                worker_name=self.worker_name, heartbeat_at=cycle_started.isoformat(),
                lease_state="BUSY", state="DEGRADED", last_error_code="LEASE_BUSY")
            return {"skipped": True, "reason": "LEASE_BUSY", "symbols": 0, "persisted": 0, "errors": 0}
        runtime_started(self.worker_name)
        persisted = source_persisted = errors = samples_collected = samples_rejected = symbols_succeeded = 0
        symbols = self._symbols()
        source_health = {key: {"attempted": 0, "succeeded": 0, "failed": 0}
                         for key in ("DEPTH", "FUNDING", "OPEN_INTEREST")}
        previous = self.repository.worker_health() or {}
        self.repository.update_worker_health(
            worker_name=self.worker_name, configured_value=self.configured_value,
            configured_enabled=True, effective_enabled=True, state="WAITING_FOR_FIRST_SAMPLE",
            worker_started_at=previous.get("worker_started_at") or cycle_started.isoformat(),
            heartbeat_at=cycle_started.isoformat(), lease_state="ACQUIRED", lease_owner=self.owner_id,
            active_symbols=symbols, last_cycle_started_at=cycle_started.isoformat(),
            source_health=source_health, last_error_code=None,
        )
        adapter: BingXSwapAdapter | None = None
        try:
            adapter = _public_bingx_adapter()
            for symbol in symbols:
                if not acquire_lease(self.worker_name, self.owner_id, ttl):
                    raise RuntimeError("MICROSTRUCTURE_OBSERVER_LEASE_LOST")
                aggregate = None
                symbol_success = False
                source_health["DEPTH"]["attempted"] += 1
                try:
                    for sample_index in range(self.samples_per_symbol):
                        snapshot = await asyncio.wait_for(
                            adapter.market_depth(symbol, self.max_levels),
                            timeout=float(os.getenv("EXCHANGE_HTTP_TIMEOUT", "10")) + 2,
                        )
                        aggregate = self.buffer.ingest(symbol, snapshot)
                        samples_collected += 1
                        if sample_index + 1 < self.samples_per_symbol:
                            await asyncio.sleep(self.sample_spacing_ms / 1000)
                    source_health["DEPTH"]["succeeded"] += 1
                    self.repository.record_source_health(symbol=symbol, source_type="DEPTH",
                                                         provider="BINGX_PUBLIC_FUTURES_DEPTH", success=True)
                    symbol_success = True
                    logging.info(
                        "microstructure_stage=complete symbol=%s samples=%s status=%s",
                        symbol,
                        self.samples_per_symbol,
                        (aggregate or {}).get("status", "UNAVAILABLE"),
                    )
                except Exception as exc:
                    errors += 1
                    samples_rejected += 1
                    source_health["DEPTH"]["failed"] += 1
                    self.repository.record_source_health(
                        symbol=symbol, source_type="DEPTH", provider="BINGX_PUBLIC_FUTURES_DEPTH",
                        success=False, error_code=self._error_code("DEPTH", exc))
                    logging.warning(
                        "microstructure_stage=failed symbol=%s error_code=%s",
                        symbol,
                        type(exc).__name__,
                        exc_info=True,
                    )

                derivatives = await self._derivative_requests(adapter, symbol)
                derivative_context: dict[str, Any] = {"status": "UNAVAILABLE", "freshness": "UNAVAILABLE",
                                                      "source": "BINGX_PUBLIC_FUTURES_MARKET"}
                for source_type, value in derivatives.items():
                    source_health[source_type]["attempted"] += 1
                    provider = ("BINGX_PUBLIC_FUTURES_FUNDING" if source_type == "FUNDING"
                                else "BINGX_PUBLIC_FUTURES_OPEN_INTEREST")
                    if isinstance(value, Exception):
                        errors += 1
                        samples_rejected += 1
                        source_health[source_type]["failed"] += 1
                        code = self._error_code(source_type, value)
                        self.repository.record_source_health(symbol=symbol, source_type=source_type,
                                                             provider=provider, success=False, error_code=code)
                        derivative_context[f"{source_type.lower()}_status"] = "UNAVAILABLE"
                        derivative_context[f"{source_type.lower()}_reason_code"] = code
                        continue
                    payload = dict(value)
                    if source_type == "OPEN_INTEREST" and aggregate:
                        payload["reference_price"] = aggregate.get("mid_price")
                    try:
                        inserted = self.repository.persist_source_snapshot(
                            symbol=symbol, exchange="bingx",
                            environment=getattr(adapter, "environment", "unknown"),
                            source_type=source_type, provider=provider, snapshot=payload,
                            ttl_seconds=max(self.interval_seconds * 3, 90))
                        source_persisted += int(inserted)
                        samples_collected += 1
                        source_health[source_type]["succeeded"] += 1
                        self.repository.record_source_health(symbol=symbol, source_type=source_type,
                                                             provider=provider, success=True)
                        derivative_context.update(payload)
                        derivative_context[f"{source_type.lower()}_status"] = "AVAILABLE"
                        symbol_success = True
                    except Exception as exc:
                        errors += 1
                        samples_rejected += 1
                        source_health[source_type]["failed"] += 1
                        code = self._error_code(f"{source_type}_PERSIST", exc)
                        self.repository.record_source_health(symbol=symbol, source_type=source_type,
                                                             provider=provider, success=False, error_code=code)
                        derivative_context[f"{source_type.lower()}_status"] = "UNAVAILABLE"
                        derivative_context[f"{source_type.lower()}_reason_code"] = code
                if aggregate is not None:
                    derivative_context["status"] = "AVAILABLE" if (
                        derivative_context.get("funding_rate") is not None or
                        derivative_context.get("open_interest") is not None) else "UNAVAILABLE"
                    derivative_context["freshness"] = "FRESH" if derivative_context["status"] == "AVAILABLE" else "UNAVAILABLE"
                    aggregate["funding_open_interest"] = derivative_context
                    try:
                        inserted = self.repository.persist_microstructure(
                            symbol=symbol, exchange="bingx",
                            environment=getattr(adapter, "environment", "unknown"),
                            aggregate=aggregate, ttl_seconds=max(self.interval_seconds * 3, 90))
                        persisted += int(inserted)
                    except Exception as exc:
                        errors += 1
                        samples_rejected += 1
                        source_health["DEPTH"]["failed"] += 1
                        code = self._error_code("DEPTH_PERSIST", exc)
                        self.repository.record_source_health(
                            symbol=symbol, source_type="DEPTH", provider="BINGX_PUBLIC_FUTURES_DEPTH",
                            success=False, error_code=code)
                symbols_succeeded += int(symbol_success)

            completed = datetime.now(timezone.utc)
            successful_sources = sum(item["succeeded"] for item in source_health.values())
            attempted_sources = sum(item["attempted"] for item in source_health.values())
            failed_sources = sum(item["failed"] for item in source_health.values())
            state = ("HEALTHY" if attempted_sources and successful_sources == attempted_sources and not failed_sources else
                     "DEGRADED" if successful_sources else "FAILED")
            last_error_code = next((f"{key}_FAILED" for key, item in source_health.items()
                                    if item["failed"]), None)
            consecutive_failures = 0 if state == "HEALTHY" else int(previous.get("consecutive_failures") or 0) + 1
            last_success = completed.isoformat()
            details = {"skipped": False, "symbols": len(symbols), "persisted": persisted,
                       "source_persisted": source_persisted,
                       "errors": errors, "bounded": True, "lease_ttl_seconds": ttl,
                       "state": state, "samples_collected": samples_collected,
                       "samples_rejected": samples_rejected, "symbols_succeeded": symbols_succeeded,
                       "source_health": source_health,
                       "cycle_duration_ms": round((completed - cycle_started).total_seconds() * 1000, 3)}
            self.repository.update_worker_health(
                worker_name=self.worker_name, state=state, heartbeat_at=completed.isoformat(),
                lease_state="RELEASED", lease_owner=self.owner_id, active_symbols=symbols,
                source_health=source_health, last_cycle_completed_at=completed.isoformat(),
                last_depth_success_at=(last_success if source_health["DEPTH"]["succeeded"] else previous.get("last_depth_success_at")),
                last_funding_success_at=(last_success if source_health["FUNDING"]["succeeded"] else previous.get("last_funding_success_at")),
                last_oi_success_at=(last_success if source_health["OPEN_INTEREST"]["succeeded"] else previous.get("last_oi_success_at")),
                last_persist_success_at=(last_success if persisted or source_persisted else previous.get("last_persist_success_at")),
                last_error_code=last_error_code, consecutive_failures=consecutive_failures,
                samples_collected=samples_collected, samples_rejected=samples_rejected,
                symbols_attempted=len(symbols), symbols_succeeded=symbols_succeeded,
                cycle_duration_ms=details["cycle_duration_ms"])
            if state in {"DEGRADED", "FAILED"}:
                await self._deliver_provider_alerts(symbols, source_health)
            runtime_finished(self.worker_name, processed=len(symbols), errors=errors, details=details)
            return details
        except Exception as exc:
            self.repository.update_worker_health(
                worker_name=self.worker_name, state="FAILED",
                heartbeat_at=datetime.now(timezone.utc).isoformat(), lease_state="RELEASED",
                last_error_code=self._error_code("WORKER", exc),
                consecutive_failures=int(previous.get("consecutive_failures") or 0) + 1)
            runtime_finished(self.worker_name, processed=0, errors=1,
                             error=self._error_code("WORKER", exc))
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
        now = datetime.now(timezone.utc).isoformat()
        self.repository.update_worker_health(
            worker_name=self.worker_name, worker_started_at=now, heartbeat_at=now,
            state="WAITING_FOR_FIRST_SAMPLE" if self.enabled else "DISABLED_BY_CONFIGURATION")
        while not self._stop.is_set():
            try:
                await self.check_once()
            except Exception:
                logging.exception("Microstructure observer cycle failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                pass
