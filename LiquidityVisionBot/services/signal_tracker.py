import asyncio
import logging
import os
import time
import uuid
from datetime import datetime, timezone

from aiogram import Bot

from database.signal_history import SignalHistory
from services.market import Market
from services.notifier import Notifier


class SignalTracker:
    """Resilient background monitor for signal lifecycle transitions."""

    def __init__(
        self,
        interval_seconds: int = 60,
        bot: Bot | None = None,
        batch_size: int = 25,
        concurrency: int = 5,
    ):
        self.interval_seconds = max(30, interval_seconds)
        self.batch_size = max(1, batch_size)
        self.concurrency = max(1, concurrency)
        self.worker_id = f"{os.getenv('RENDER_INSTANCE_ID', 'local')}-{uuid.uuid4().hex[:8]}"
        self.history = SignalHistory()
        self.market = Market()
        self.notifier = Notifier(bot)
        self._stopping = asyncio.Event()

    @staticmethod
    def _reached(side: str, price: float, level: float) -> bool:
        return price >= level if side == "LONG" else price <= level

    @staticmethod
    def _stop_hit(side: str, price: float, stop: float) -> bool:
        return price <= stop if side == "LONG" else price >= stop

    @staticmethod
    def _in_zone(price: float, low: float | None, high: float | None) -> bool:
        if low is None or high is None:
            return False
        return min(low, high) <= price <= max(low, high)

    @staticmethod
    def _directional_candle(side: str, row) -> bool:
        open_price = float(row["open"])
        close_price = float(row["close"])
        high = float(row["high"])
        low = float(row["low"])
        candle_range = max(high - low, 1e-12)
        body_ratio = abs(close_price - open_price) / candle_range
        aligned = close_price > open_price if side == "LONG" else close_price < open_price
        return aligned and body_ratio >= 0.45

    async def _transition(self, signal: dict, event: str, price: float, **fields) -> bool:
        previous = signal["status"]
        changed = self.history.transition_if_current(
            signal["id"],
            (previous,),
            event,
            price,
            details={"worker_id": self.worker_id},
            **fields,
        )
        if not changed:
            logging.info(
                "Signal %s transition %s -> %s skipped because state changed elsewhere",
                signal["id"], previous, event,
            )
            return False

        signal["status"] = event
        if signal.get("last_notified_status") != event:
            await self.notifier.lifecycle(signal, event, price)
            self.history.update_lifecycle(signal["id"], last_notified_status=event)
            signal["last_notified_status"] = event
        return True

    async def _update(self, signal: dict, price: float, df) -> None:
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        side = signal["side"]
        status = signal["status"]
        entry = float(signal["entry"])
        stop = float(signal["stop"])

        expires_at = signal.get("expires_at")
        if status in {"WATCHING", "TRIGGERED"} and expires_at:
            try:
                if now_dt >= datetime.fromisoformat(expires_at):
                    await self._transition(signal, "EXPIRED", price, closed_at=now)
                    return
            except (ValueError, TypeError):
                logging.warning("Signal %s has invalid expires_at=%r", signal["id"], expires_at)

        # Before activation, crossing invalidation cancels the setup instead of logging a loss.
        if status in {"WATCHING", "TRIGGERED"} and self._stop_hit(side, price, stop):
            await self._transition(signal, "INVALIDATED", price, invalidated_at=now, closed_at=now)
            return

        zone_low = signal.get("preferred_entry_low")
        zone_high = signal.get("preferred_entry_high")
        if status == "WATCHING":
            if self._in_zone(price, zone_low, zone_high):
                await self._transition(signal, "TRIGGERED", price, triggered_at=now)
                return
            no_zone = zone_low is None or zone_high is None
            crossed_entry = price >= entry if side == "LONG" else price <= entry
            if no_zone and crossed_entry:
                await self._transition(signal, "ACTIVE", price, activated_at=now)
                return

        if status == "TRIGGERED":
            # Confirm only with a completed one-minute candle.
            candle = df.iloc[-2] if len(df) >= 2 else df.iloc[-1]
            if self._directional_candle(side, candle):
                await self._transition(signal, "ACTIVE", price, activated_at=now)
                return

        if signal["status"] not in {"ACTIVE", "TP1", "TP2"}:
            self.history.update_lifecycle(signal["id"], current_price=price)
            return

        move_pct = ((price - entry) / entry * 100) * (1 if side == "LONG" else -1)
        max_profit = max(float(signal.get("max_profit_pct") or 0), move_pct)
        max_drawdown = min(float(signal.get("max_drawdown_pct") or 0), move_pct)
        common = {"current_price": price, "max_profit_pct": max_profit, "max_drawdown_pct": max_drawdown}

        if self._stop_hit(side, price, stop):
            await self._transition(signal, "STOP", price, stop_hit_at=now, closed_at=now, **common)
            return

        # Evaluate highest target first so a fast candle cannot require three monitor cycles.
        if not signal.get("tp3_hit_at") and self._reached(side, price, float(signal["tp3"])):
            fields = {
                **common,
                "tp1_hit_at": signal.get("tp1_hit_at") or now,
                "tp2_hit_at": signal.get("tp2_hit_at") or now,
                "tp3_hit_at": now,
                "closed_at": now,
            }
            await self._transition(signal, "TP3", price, **fields)
            return
        if not signal.get("tp2_hit_at") and self._reached(side, price, float(signal["tp2"])):
            fields = {**common, "tp1_hit_at": signal.get("tp1_hit_at") or now, "tp2_hit_at": now}
            await self._transition(signal, "TP2", price, **fields)
            return
        if not signal.get("tp1_hit_at") and self._reached(side, price, float(signal["tp1"])):
            await self._transition(signal, "TP1", price, tp1_hit_at=now, **common)
            return
        self.history.update_lifecycle(signal["id"], **common)

    async def _process_claimed(self, signal: dict, semaphore: asyncio.Semaphore) -> bool:
        async with semaphore:
            try:
                df = await self.market.get_klines(signal["symbol"], "1m", 6)
                if df is None or len(df) < 2:
                    raise RuntimeError("Market provider returned insufficient 1m candles")
                price = float(df["close"].iloc[-1])
                await self._update(signal, price, df)
                self.history.release_claim(
                    signal["id"], self.worker_id, next_check_seconds=self.interval_seconds
                )
                return True
            except asyncio.CancelledError:
                self.history.release_claim(signal["id"], self.worker_id, next_check_seconds=10)
                raise
            except Exception as exc:
                errors = int(signal.get("consecutive_errors") or 0) + 1
                backoff = min(self.interval_seconds * (2 ** min(errors, 5)), 1800)
                self.history.release_claim(
                    signal["id"], self.worker_id, next_check_seconds=backoff, error=str(exc)
                )
                logging.exception("Failed to update signal %s", signal["id"])
                return False

    async def check_once(self) -> tuple[int, int, int]:
        started = time.perf_counter()
        signals = self.history.claim_due(
            self.worker_id,
            limit=self.batch_size,
            lease_seconds=max(self.interval_seconds * 2, 90),
        )
        if not signals:
            self.history.record_monitor_run(self.worker_id, 0, 0, 0, 0)
            return 0, 0, 0

        semaphore = asyncio.Semaphore(self.concurrency)
        results = await asyncio.gather(
            *(self._process_claimed(signal, semaphore) for signal in signals),
            return_exceptions=False,
        )
        succeeded = sum(1 for result in results if result)
        failed = len(results) - succeeded
        duration_ms = int((time.perf_counter() - started) * 1000)
        self.history.record_monitor_run(self.worker_id, len(signals), succeeded, failed, duration_ms)
        logging.info(
            "Monitor cycle worker=%s checked=%s succeeded=%s failed=%s duration_ms=%s",
            self.worker_id, len(signals), succeeded, failed, duration_ms,
        )
        return len(signals), succeeded, failed

    async def run_forever(self) -> None:
        logging.info("Signal monitor started worker=%s interval=%ss", self.worker_id, self.interval_seconds)
        try:
            while not self._stopping.is_set():
                cycle_started = time.monotonic()
                try:
                    await self.check_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logging.exception("Signal monitor cycle failed")

                elapsed = time.monotonic() - cycle_started
                delay = max(1.0, self.interval_seconds - elapsed)
                try:
                    await asyncio.wait_for(self._stopping.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
        finally:
            logging.info("Signal monitor stopped worker=%s", self.worker_id)

    def stop(self) -> None:
        self._stopping.set()
