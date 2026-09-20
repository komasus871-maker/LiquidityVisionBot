"""Continuous, lightweight anomaly monitor for the Telegram web service."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp
from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from services.forward_runtime_state import ForwardRuntimeStateRepository
from services.pump_dump_scanner import (
    Candle, PumpDumpScanner, ScannerRepository, ScannerSettings,
    build_symbol_snapshot, render_alert_card, resource_budget, select_liquid_universe,
    classify_additional_alerts,
)
from services.user_watchlist import UserWatchlist


class BinanceFuturesBroadFeed:
    """REST-only public feed; intentionally never opens full-depth streams."""
    base_url = "https://fapi.binance.com"

    def __init__(self) -> None:
        self._contracts: dict[str, str] = {}
        self._contracts_loaded_at = 0.0

    async def _json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        timeout = aiohttp.ClientTimeout(total=12)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{self.base_url}{path}", params=params) as response:
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
        self._stop = asyncio.Event()

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
            [InlineKeyboardButton(text="🔕 Mute", callback_data=f"pdmute:{symbol}"),
             InlineKeyboardButton(text="⚙ Scanner Settings", callback_data="pdsettings")],
        ])

    @staticmethod
    def _enrichment(rows: list[dict[str, Any]], symbol: str) -> dict[str, Any]:
        selected = [row for row in rows if row.get("symbol") == symbol]
        if not selected:
            return {}
        freshest = max(selected, key=lambda row: row.get("observed_at") or "")
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

    async def check_once(self) -> dict[str, Any]:
        subscribers = self.repository.subscribers()
        self.repository.close_inactive(
            older_than=datetime.now(timezone.utc) - timedelta(
                seconds=max(300, self.interval_seconds * 3)
            )
        )
        if not self.enabled:
            return {"status": "disabled", **resource_budget()}
        if not subscribers:
            return {"status": "idle", "reason": "NO_SUBSCRIBERS", **resource_budget()}
        instruments = await self.feed.instruments()
        minimum = min(settings.minimum_quote_volume_24h for _, settings in subscribers)
        universe = select_liquid_universe(instruments, minimum_quote_volume=minimum,
                                          limit=self.universe_limit)
        ticker_by_symbol = {str(item["symbol"]): item for item in instruments}
        deep_rows = self.forward.latest_states(("BTCUSDT", "ETHUSDT", "SOLUSDT"))
        semaphore = asyncio.Semaphore(self.concurrency)

        async def one(symbol: str):
            async with semaphore:
                try:
                    candles = await self.feed.candles(symbol)
                    ticker = ticker_by_symbol[symbol]
                    return build_symbol_snapshot(
                        symbol=symbol, venue="BINANCE", candles=candles,
                        change_24h_pct=float(ticker.get("change_24h_pct") or 0),
                        quote_volume_24h=float(ticker.get("quote_volume") or 0),
                        enrichment=self._enrichment(deep_rows, symbol),
                    )
                except Exception as exc:
                    logging.warning("Pump/dump snapshot failed for %s: %s", symbol, exc)
                    return None

        snapshots = [item for item in await asyncio.gather(*(one(symbol) for symbol in universe)) if item]
        delivered, admitted = 0, 0
        for telegram_id, settings in subscribers:
            allowed = set(universe)
            if settings.market_scope == "WATCHLIST_ONLY":
                allowed &= {str(row["symbol"]) for row in self.watchlist.list(telegram_id)}
            elif settings.market_scope == "CUSTOM":
                allowed &= set(settings.custom_symbols)
            for snapshot in snapshots:
                if snapshot.symbol not in allowed:
                    continue
                candidates = self.detector.detect(snapshot, settings)
                severity_rank = {"NORMAL": 0, "STRONG": 1, "EXTREME": 2}
                candidates = sorted(
                    candidates,
                    key=lambda item: (severity_rank[item["severity"].value], abs(item["move_pct"])),
                    reverse=True,
                )[:1]
                for candidate in candidates:
                    alert = self.repository.admit(candidate, settings, telegram_id=telegram_id)
                    if not alert:
                        continue
                    admitted += 1
                    if self.bot is not None:
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
                    if enabled_aux.get(extra["alert_type"], True) is False:
                        continue
                    if self.repository.record_auxiliary(snapshot, extra, telegram_id=telegram_id):
                        new_auxiliary.append(extra["alert_type"])
                        admitted += 1
                if new_auxiliary and self.bot is not None:
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
        return {"status": "ok", "universe": len(universe), "snapshots": len(snapshots),
                "events": admitted, "delivered": delivered, **resource_budget()}

    async def run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                await self.check_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.exception("Pump/dump monitor cycle failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                pass
