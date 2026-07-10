import asyncio
import logging
from datetime import datetime, timezone

from aiogram import Bot

from database.signal_history import SignalHistory
from services.market import Market
from services.notifier import Notifier


class SignalTracker:
    def __init__(self, interval_seconds: int = 60, bot: Bot | None = None):
        self.interval_seconds = max(30, interval_seconds)
        self.history = SignalHistory()
        self.market = Market()
        self.notifier = Notifier(bot)

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

    async def check_once(self) -> None:
        for signal in self.history.get_open():
            try:
                df = await self.market.get_klines(signal["symbol"], "1m", 5)
                price = float(df["close"].iloc[-1])
                await self._update(signal, price, df)
            except Exception:
                logging.exception("Failed to update signal %s", signal["id"])

    async def _transition(self, signal: dict, event: str, price: float, **fields) -> None:
        previous = signal["status"]
        fields["status"] = event
        self.history.update_lifecycle(signal["id"], **fields)
        self.history.add_event(signal["id"], event, price, {"from": previous})
        if signal.get("last_notified_status") != event:
            await self.notifier.lifecycle(signal, event, price)
            self.history.update_lifecycle(signal["id"], last_notified_status=event)
        signal["status"] = event

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
            except ValueError:
                pass

        # Before activation, crossing the invalidation level cancels the idea rather than logging a trade loss.
        if status in {"WATCHING", "TRIGGERED"} and self._stop_hit(side, price, stop):
            await self._transition(signal, "INVALIDATED", price, invalidated_at=now, closed_at=now)
            return

        zone_low = signal.get("preferred_entry_low")
        zone_high = signal.get("preferred_entry_high")
        if status == "WATCHING":
            if self._in_zone(price, zone_low, zone_high):
                await self._transition(signal, "TRIGGERED", price, triggered_at=now)
                return
            # READY setups are stored as ACTIVE and never arrive here. Trigger-only ideas without a zone
            # can activate when price trades through the planned entry.
            no_zone = zone_low is None or zone_high is None
            crossed_entry = price >= entry if side == "LONG" else price <= entry
            if no_zone and crossed_entry:
                await self._transition(signal, "ACTIVE", price, activated_at=now)
                return

        if status == "TRIGGERED":
            # Use the latest completed directional 1m candle as a lightweight reaction confirmation.
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
            signal.update(common)
            await self._transition(signal, "STOP", price, stop_hit_at=now, closed_at=now, **common)
            return

        # Handle gaps and fast moves without waiting one monitor cycle per target.
        if not signal.get("tp3_hit_at") and self._reached(side, price, float(signal["tp3"])):
            common.update({
                "tp1_hit_at": signal.get("tp1_hit_at") or now,
                "tp2_hit_at": signal.get("tp2_hit_at") or now,
                "tp3_hit_at": now,
                "closed_at": now,
            })
            signal.update(common)
            await self._transition(signal, "TP3", price, **common)
            return
        if not signal.get("tp2_hit_at") and self._reached(side, price, float(signal["tp2"])):
            common.update({"tp1_hit_at": signal.get("tp1_hit_at") or now, "tp2_hit_at": now})
            signal.update(common)
            await self._transition(signal, "TP2", price, **common)
            return
        if not signal.get("tp1_hit_at") and self._reached(side, price, float(signal["tp1"])):
            common.update({"tp1_hit_at": now})
            signal.update(common)
            await self._transition(signal, "TP1", price, **common)
            return
        self.history.update_lifecycle(signal["id"], **common)

    async def run_forever(self) -> None:
        while True:
            await self.check_once()
            await asyncio.sleep(self.interval_seconds)
