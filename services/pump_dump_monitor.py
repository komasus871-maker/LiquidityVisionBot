"""Continuous, lightweight anomaly monitor for the Telegram web service."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import statistics
import time
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp
from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from database.database import acquire_lease, release_lease, runtime_finished, runtime_started
from services.forward_runtime_state import ForwardRuntimeStateRepository
from services.pump_dump_scanner import (
    Candle, PumpDumpScanner, ScannerRepository, ScannerSettings,
    build_symbol_snapshot, render_alert_card, resource_budget, select_liquid_universe,
    classify_additional_alerts,
)
from services.user_watchlist import UserWatchlist
from services.runtime_supervision import ProviderRegionBlockedError, bounded_thread_call


class BinanceFuturesBroadFeed:
    """REST-only public feed; intentionally never opens full-depth streams."""
    base_url = "https://fapi.binance.com"

    def __init__(self) -> None:
        self._contracts: dict[str, str] = {}
        self._contracts_loaded_at = 0.0
        self._session: aiohttp.ClientSession | None = None
        self._region_blocked_until = 0.0
        self._region_blocked_until_at: str | None = None
        self.region_block_backoff_seconds = max(
            300.0, float(os.getenv("PROVIDER_REGION_BLOCK_BACKOFF_SECONDS", "3600")),
        )

    @property
    def region_blocked_until_at(self) -> str | None:
        return self._region_blocked_until_at

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        loop = asyncio.get_running_loop()
        if loop.time() < self._region_blocked_until:
            raise ProviderRegionBlockedError("BINANCE")
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=12)
            connector = aiohttp.TCPConnector(limit=20, ttl_dns_cache=300)
            self._session = aiohttp.ClientSession(timeout=timeout, connector=connector)
        async with self._session.get(f"{self.base_url}{path}", params=params) as response:
            if response.status == 451:
                self._region_blocked_until = loop.time() + self.region_block_backoff_seconds
                self._region_blocked_until_at = (
                    datetime.now(timezone.utc) + timedelta(seconds=self.region_block_backoff_seconds)
                ).isoformat()
                raise ProviderRegionBlockedError("BINANCE", response.status)
            response.raise_for_status()
            return await response.json()

    async def instruments(self) -> list[dict[str, Any]]:
        loop = asyncio.get_running_loop()
        if not self._contracts or loop.time() - self._contracts_loaded_at > 3600:
            info = await self._json("/fapi/v1/exchangeInfo")
            self._contracts = {
                str(item["symbol"]): str(item.get("contractType") or "")
                for item in info.get("symbols", [])
                if item.get("status") == "TRADING"
            }
            self._contracts_loaded_at = loop.time()
        tickers = await self._json("/fapi/v1/ticker/24hr")
        return [{
            "symbol": item.get("symbol"), "status": "TRADING",
            "contract_type": self._contracts.get(str(item.get("symbol")), ""),
            "quote_volume": item.get("quoteVolume"),
            "change_24h_pct": item.get("priceChangePercent"),
        } for item in tickers if item.get("symbol") in self._contracts]

    async def candles(self, symbol: str) -> list[Candle]:
        rows = await self._json("/fapi/v1/klines", {"symbol": symbol, "interval": "1m", "limit": 241})
        return [Candle(
            opened_at=datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc),
            open=float(row[1]), high=float(row[2]), low=float(row[3]), close=float(row[4]),
            quote_volume=float(row[7]), trade_count=int(row[8]),
        ) for row in rows]


class PumpDumpMonitor:
    worker_name = "pump-dump-market-alert-monitor"

    def __init__(self, bot: Bot | None = None, feed: BinanceFuturesBroadFeed | None = None) -> None:
        self.bot = bot
        self.feed = feed or BinanceFuturesBroadFeed()
        self.detector = PumpDumpScanner()
        self.repository = ScannerRepository()
        self.forward = ForwardRuntimeStateRepository()
        self.watchlist = UserWatchlist()
        self.interval_seconds = max(30, int(os.getenv("PUMP_SCANNER_INTERVAL_SECONDS", "60")))
        self.universe_limit = max(5, min(100, int(os.getenv("PUMP_SCANNER_UNIVERSE_LIMIT", "40"))))
        self.concurrency = max(1, min(10, int(os.getenv("PUMP_SCANNER_CONCURRENCY", "5"))))
        self.enabled = os.getenv("PUMP_SCANNER_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
        self.cycle_timeout_seconds = max(
            30, int(os.getenv("PUMP_SCANNER_CYCLE_TIMEOUT_SECONDS", "110")),
        )
        self._stop = asyncio.Event()
        self.owner_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self.current_stage = "idle"
        self.last_progress_monotonic = time.monotonic()
        self.last_progress_at = datetime.now(timezone.utc).isoformat()
        self.last_success_at: str | None = None
        self.last_error: str | None = None

    def _progress(self, stage: str) -> None:
        self.current_stage = stage
        self.last_progress_monotonic = time.monotonic()
        self.last_progress_at = datetime.now(timezone.utc).isoformat()

    def stop(self) -> None:
        self._stop.set()

    @staticmethod
    def _buttons(symbol: str) -> InlineKeyboardMarkup:
        base = symbol.removesuffix("USDT")
        terminal_url = (os.getenv("WEBHOOK_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL")
                        or "https://liquidityvisionbot-1.onrender.com").rstrip("/") + "/terminal"
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📊 Analyze", callback_data=f"analyze_{base}"),
             InlineKeyboardButton(text="🔬 Deep Analyze", callback_data=f"deep:{base}:1h")],
            [InlineKeyboardButton(text="🌊 Order Flow", callback_data=f"flow:{symbol}"),
             InlineKeyboardButton(text="⭐ Watchlist", callback_data=f"watch:{base}:1h")],
            [InlineKeyboardButton(text="📈 Chart", url=f"https://www.binance.com/en/futures/{symbol}"),
             InlineKeyboardButton(text="🚀 Terminal", web_app=WebAppInfo(url=terminal_url))],
            [InlineKeyboardButton(text="⚡ Open Scanner", callback_data="scanhome"),
             InlineKeyboardButton(text="🔕 Mute", callback_data=f"pdmute:{symbol}")],
            [
             InlineKeyboardButton(text="⚙ Scanner Settings", callback_data="pdsettings")],
        ])

    @staticmethod
    def _enrichment(rows: list[dict[str, Any]], symbol: str) -> dict[str, Any]:
        selected = [row for row in rows if row.get("symbol") == symbol]
        if not selected:
            return {}
        freshest = max(selected, key=lambda row: row.get("observed_at") or "")
        try:
            observed = datetime.fromisoformat(
                str(freshest.get("observed_at") or "").replace("Z", "+00:00")
            )
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=timezone.utc)
            max_age = max(30, int(os.getenv("FORWARD_ENRICHMENT_MAX_AGE_SECONDS", "120")))
            if (datetime.now(timezone.utc) - observed).total_seconds() > max_age:
                return {}
        except (TypeError, ValueError):
            return {}
        snap = freshest.get("snapshot") or {}
        book = snap.get("book") or {}
        flow = ((snap.get("trade_flow") or {}).get("horizons_ms") or {}).get("60000") or {}
        derivatives = snap.get("derivatives") or {}
        liq = (snap.get("liquidations") or {}).get("60000") or {}
        cross = snap.get("cross_venue") or {}
        buy_liq = float(liq.get("forced_buy_notional") or 0)
        sell_liq = float(liq.get("forced_sell_notional") or 0)
        return {
            "oi_change_pct": (float(derivatives["open_interest_change"]) * 100
                              if derivatives.get("open_interest_change") is not None else None),
            "funding_rate": derivatives.get("funding_rate"),
            "basis_pct": ((float(derivatives["basis_bps"]) / 100)
                          if derivatives.get("basis_bps") is not None else None),
            "cvd": flow.get("delta_notional"),
            "taker_imbalance": flow.get("normalized_delta"),
            "liquidations_usd": buy_liq + sell_liq,
            "liquidation_bias": ("short-heavy" if buy_liq > sell_liq else "long-heavy")
                                if buy_liq + sell_liq else None,
            "spread_pct": ((float(book["spread_bps"]) / 100)
                           if book.get("spread_bps") is not None else None),
            "book_imbalance": book.get("depth_imbalance"),
            "cross_venue_diff_pct": ((float(cross["dispersion_bps"]) / 100)
                                     if cross.get("dispersion_bps") is not None else None),
            "data_quality": freshest.get("data_quality") or "ENRICHED",
        }

    async def _check_once_owned(self) -> dict[str, Any]:
        cycle_started = datetime.now(timezone.utc)
        cycle_timer = time.perf_counter()
        stages: dict[str, str] = {"cycle_started_at": cycle_started.isoformat()}
        self._progress("loading_scanner_state")
        subscribers = await bounded_thread_call(self.repository.subscribers)
        await bounded_thread_call(
            self.repository.close_inactive,
            older_than=datetime.now(timezone.utc) - timedelta(
                seconds=max(300, self.interval_seconds * 3)
            ),
        )
        if not self.enabled:
            return {"status": "disabled", **resource_budget()}
        self._progress("discovering_universe")
        try:
            instruments = await self.feed.instruments()
        except ProviderRegionBlockedError as exc:
            self.last_error = str(exc)
            self._progress("provider_region_blocked")
            return {
                "status": "degraded", "reason": str(exc),
                "provider": exc.provider, "provider_state": "REGION_BLOCKED",
                "provider_retry_at": getattr(self.feed, "region_blocked_until_at", None),
                "universe": 0, "snapshots": 0, "successfully_fetched": 0,
                "failed_symbol_count": 0, "baseline_ready_symbols": 0,
                "shortlisted_symbols": 0, "deep_enrichment_symbols": 0,
                "current_stage": self.current_stage,
                "cycle_started_at": cycle_started.isoformat(),
                **resource_budget(),
            }
        stages["universe_discovered_at"] = datetime.now(timezone.utc).isoformat()
        # The broad radar is an authoritative product data plane, not a
        # notification side effect.  It must run even before any user has
        # persisted scanner preferences.
        global_settings = ScannerSettings()
        minimum = min(
            [global_settings.minimum_quote_volume_24h]
            + [settings.minimum_quote_volume_24h for _, settings in subscribers]
        )
        universe = select_liquid_universe(instruments, minimum_quote_volume=minimum,
                                          limit=self.universe_limit)
        ticker_by_symbol = {str(item["symbol"]): item for item in instruments}
        semaphore = asyncio.Semaphore(self.concurrency)

        async def one(symbol: str):
            async with semaphore:
                try:
                    candles = await self.feed.candles(symbol)
                    ticker = ticker_by_symbol[symbol]
                    return symbol, build_symbol_snapshot(
                        symbol=symbol, venue="BINANCE", candles=candles,
                        change_24h_pct=float(ticker.get("change_24h_pct") or 0),
                        quote_volume_24h=float(ticker.get("quote_volume") or 0),
                    ), None
                except Exception as exc:
                    logging.warning("Pump/dump snapshot failed for %s: %s", symbol, exc)
                    return symbol, None, f"{type(exc).__name__}: {str(exc)[:120]}"

        self._progress("fetching_1m_history")
        fetched = await asyncio.gather(*(one(symbol) for symbol in universe))
        snapshots = [item for _, item, _ in fetched if item is not None]
        failed_symbols = {symbol: error for symbol, item, error in fetched if item is None}
        stages["broad_radar_completed_at"] = datetime.now(timezone.utc).isoformat()
        benchmark_5m = {item.symbol: item.changes_pct.get(5) for item in snapshots}
        market_values = [float(value) for value in benchmark_5m.values() if value is not None]
        market_move = statistics.median(market_values) if market_values else None
        btc_move, eth_move = benchmark_5m.get("BTCUSDT"), benchmark_5m.get("ETHUSDT")
        magnitudes = sorted(abs(value) for value in market_values)
        snapshots = [replace(
            item,
            btc_relative_return_pct=(
                round(item.changes_pct[5] - btc_move, 4) if btc_move is not None else None
            ),
            eth_relative_return_pct=(
                round(item.changes_pct[5] - eth_move, 4) if eth_move is not None else None
            ),
            market_relative_return_pct=(
                round(item.changes_pct[5] - market_move, 4) if market_move is not None else None
            ),
            cross_sectional_percentile=(
                round(100 * sum(value <= abs(item.changes_pct[5]) for value in magnitudes)
                      / len(magnitudes), 1) if magnitudes else None
            ),
        ) for item in snapshots]

        self._progress("advancing_outcomes")
        outcome_updates = {"updated": 0, "finalized": 0}
        for snapshot in snapshots:
            advanced = await bounded_thread_call(self.repository.advance_outcomes, snapshot)
            outcome_updates["updated"] += advanced["updated"]
            outcome_updates["finalized"] += advanced["finalized"]

        # Stage 1 is candle/ticker-only. Only symbols that trip an adaptive
        # anomaly gate are promoted into the bounded shared microstructure state.
        shortlisted: set[str] = set()
        for settings in [global_settings, *[value for _, value in subscribers]]:
            for snapshot in snapshots:
                if self.detector.detect(snapshot, settings):
                    shortlisted.add(snapshot.symbol)
        self._progress("optional_forward_enrichment")
        deep_rows: list[dict[str, Any]] = []
        enrichment_error: str | None = None
        if shortlisted:
            try:
                deep_rows = await bounded_thread_call(
                    lambda: self.forward.latest_states(tuple(sorted(shortlisted)))
                )
            except Exception as exc:
                enrichment_error = f"{type(exc).__name__}: {str(exc)[:180]}"
                logging.warning("Forward enrichment unavailable: %s", enrichment_error)
        enriched: dict[str, Any] = {}
        for item in snapshots:
            if item.symbol not in shortlisted:
                continue
            values = self._enrichment(deep_rows, item.symbol)
            if values:
                enriched[item.symbol] = replace(item, **values)
        stages["deep_enrichment_completed_at"] = datetime.now(timezone.utc).isoformat()
        delivered, admitted, global_events = 0, 0, 0

        # Persist a single product-wide, genuine event/episode stream. User
        # settings below control personalization and delivery, not whether the
        # scanner itself exists.
        severity_rank = {"NORMAL": 0, "STRONG": 1, "EXTREME": 2}
        self._progress("persisting_global_episodes")
        for broad_snapshot in snapshots:
            snapshot = enriched.get(broad_snapshot.symbol, broad_snapshot)
            candidates = sorted(
                self.detector.detect(snapshot, global_settings),
                key=lambda item: (severity_rank[item["severity"].value], abs(item["move_pct"])),
                reverse=True,
            )[:1]
            for candidate in candidates:
                if await bounded_thread_call(
                    self.repository.admit, candidate, global_settings, telegram_id=0,
                ):
                    global_events += 1
            for extra in classify_additional_alerts(snapshot):
                if await bounded_thread_call(
                    self.repository.record_auxiliary, snapshot, extra, telegram_id=0,
                ):
                    global_events += 1

        self._progress("personalizing_and_delivering")
        for telegram_id, settings in subscribers:
            allowed = set(universe)
            if settings.market_scope == "WATCHLIST_ONLY":
                watched = await bounded_thread_call(self.watchlist.list, telegram_id)
                allowed &= {str(row["symbol"]) for row in watched}
            elif settings.market_scope == "CUSTOM":
                allowed &= set(settings.custom_symbols)
            for snapshot in snapshots:
                if snapshot.symbol not in allowed:
                    continue
                snapshot = enriched.get(snapshot.symbol, snapshot)
                candidates = self.detector.detect(snapshot, settings)
                candidates = sorted(
                    candidates,
                    key=lambda item: (severity_rank[item["severity"].value], abs(item["move_pct"])),
                    reverse=True,
                )[:1]
                for candidate in candidates:
                    alert = await bounded_thread_call(
                        self.repository.admit, candidate, settings, telegram_id=telegram_id,
                    )
                    if not alert:
                        continue
                    admitted += 1
                    if self.bot is not None and settings.notifications_enabled and not settings.quiet_mode:
                        try:
                            await self.bot.send_message(
                                telegram_id, render_alert_card(alert), parse_mode="HTML",
                                reply_markup=self._buttons(alert.symbol),
                            )
                            delivered += 1
                        except Exception as exc:
                            logging.warning("Pump/dump Telegram delivery failed user=%s: %s", telegram_id, exc)
                enabled_aux = {
                    "OI_SHOCK": settings.oi_shock_alerts,
                    "FUNDING_EXTREME": settings.funding_extreme_alerts,
                    "LIQUIDATION_CASCADE": settings.liquidation_alerts,
                }
                new_auxiliary: list[str] = []
                for extra in classify_additional_alerts(snapshot):
                    if extra["alert_type"] not in settings.enabled_alert_types:
                        continue
                    if enabled_aux.get(extra["alert_type"], True) is False:
                        continue
                    if await bounded_thread_call(
                        self.repository.record_auxiliary, snapshot, extra,
                        telegram_id=telegram_id,
                    ):
                        new_auxiliary.append(extra["alert_type"])
                        admitted += 1
                if (new_auxiliary and self.bot is not None and settings.notifications_enabled
                        and not settings.quiet_mode):
                    try:
                        labels = "\n".join(f"• {kind.replace('_', ' ')}" for kind in new_auxiliary)
                        await self.bot.send_message(
                            telegram_id,
                            f"⚠ <b>MARKET ALERT · {snapshot.symbol}</b>\n{snapshot.venue}\n\n"
                            f"{labels}\n\nDescriptive anomalies only — not an approved trade signal.",
                            parse_mode="HTML", reply_markup=self._buttons(snapshot.symbol),
                        )
                        delivered += 1
                    except Exception as exc:
                        logging.warning("Auxiliary alert delivery failed user=%s: %s", telegram_id, exc)
        stages["episode_engine_completed_at"] = datetime.now(timezone.utc).isoformat()
        self._progress("finalizing_cycle")
        outcome_counts = await bounded_thread_call(self.repository.outcome_counters)
        active_global = (await bounded_thread_call(
            self.repository.home_stats, telegram_id=0,
        ))["active_episodes"]
        cycle_duration = round(time.perf_counter() - cycle_timer, 3)
        cycle_completed = datetime.now(timezone.utc).isoformat()
        stages["cycle_completed_at"] = cycle_completed
        enrichment_status = (
            "NOT_REQUIRED" if not shortlisted else
            "HEALTHY" if len(enriched) == len(shortlisted) else "DEGRADED"
        )
        self.last_success_at = cycle_completed
        self.last_error = None
        self._progress("idle")
        return {"status": "ok", "universe": len(universe),
                "universe_target": self.universe_limit,
                "universe_candidates": len(instruments),
                "eligible_liquid_symbols": len(universe),
                "snapshots": len(snapshots), "successfully_fetched": len(snapshots),
                "failed_symbol_count": len(failed_symbols),
                "failed_symbols": failed_symbols,
                "baseline_ready_symbols": len(snapshots),
                "baseline_required_minutes": 60,
                "baseline_source": "241x1m provider REST backfill each cycle",
                "shortlisted_symbols": len(shortlisted),
                "enrichment_requested_symbols": len(shortlisted),
                "deep_enrichment_symbols": len(enriched),
                "enrichment_status": enrichment_status,
                "forward_microstructure_state": (
                    "AVAILABLE" if enrichment_status == "HEALTHY" else
                    "NOT_REQUIRED" if enrichment_status == "NOT_REQUIRED" else "UNAVAILABLE"
                ),
                "enrichment_error": enrichment_error,
                "global_events_created": global_events,
                "personalized_events": admitted, "delivered": delivered,
                "active_episodes": active_global,
                "subscribers": len(subscribers),
                "cycle_duration_seconds": cycle_duration,
                "cycle_started_at": cycle_started.isoformat(),
                "cycle_completed_at": cycle_completed,
                "current_stage": "idle",
                "pipeline_timestamps": stages,
                "outcome_labels_updated": outcome_updates["updated"],
                "outcome_labels_finalized": outcome_updates["finalized"],
                **outcome_counts,
                **resource_budget()}

    async def check_once(self) -> dict[str, Any]:
        ttl = max(self.interval_seconds * 2, 180)
        if not await bounded_thread_call(acquire_lease, self.worker_name, self.owner_id, ttl):
            return {"status": "skipped", "reason": "LEASE_BUSY", **resource_budget()}
        await bounded_thread_call(runtime_started, self.worker_name)
        try:
            result = await self._check_once_owned()
            degraded_error = (
                str(result.get("reason")) if result.get("status") == "degraded" else None
            )
            await bounded_thread_call(
                runtime_finished,
                self.worker_name, processed=int(result.get("snapshots") or 0),
                errors=int(degraded_error is not None), details=result, error=degraded_error,
            )
            return result
        except Exception as exc:
            details = {
                "status": "failed", "current_stage": self.current_stage,
                "cycle_started_at": self.last_progress_at,
            }
            await bounded_thread_call(
                runtime_finished,
                self.worker_name, processed=0, errors=1,
                error=f"{type(exc).__name__}: {exc}", details=details,
            )
            raise
        finally:
            await bounded_thread_call(release_lease, self.worker_name, self.owner_id)

    async def run_forever(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    self._progress("cycle_starting")
                    await asyncio.wait_for(
                        self.check_once(), timeout=self.cycle_timeout_seconds,
                    )
                except asyncio.CancelledError:
                    raise
                except asyncio.TimeoutError:
                    error = f"SCANNER_CYCLE_TIMEOUT:{self.cycle_timeout_seconds}s stage={self.current_stage}"
                    logging.error(error)
                    await bounded_thread_call(
                        runtime_finished, self.worker_name, processed=0, errors=1,
                        error=error, details={
                            "status": "timeout", "current_stage": self.current_stage,
                            "cycle_timeout_seconds": self.cycle_timeout_seconds,
                        },
                    )
                    self._progress("timeout_backoff")
                except Exception:
                    logging.exception("Pump/dump monitor cycle failed")
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
                except asyncio.TimeoutError:
                    pass
        finally:
            close = getattr(self.feed, "close", None)
            if callable(close):
                await close()
