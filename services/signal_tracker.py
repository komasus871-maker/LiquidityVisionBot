import asyncio
import logging
from datetime import datetime, timezone

from database.signal_history import SignalHistory
from services.market import Market


class SignalTracker:
    def __init__(self, interval_seconds: int = 60):
        self.interval_seconds = interval_seconds
        self.history = SignalHistory()
        self.market = Market()

    async def check_once(self) -> None:
        for signal in self.history.get_open():
            try:
                df = await self.market.get_klines(signal["symbol"], "1m", 5)
                price = float(df["close"].iloc[-1])
                self._update(signal, price)
            except Exception:
                logging.exception("Failed to update signal %s", signal["id"])

    def _update(self, signal: dict, price: float) -> None:
        now = datetime.now(timezone.utc).isoformat()
        entry = float(signal["entry"])
        side = signal["side"]
        move_pct = ((price - entry) / entry * 100) * (1 if side == "LONG" else -1)
        max_profit = max(float(signal["max_profit_pct"] or 0), move_pct)
        max_drawdown = min(float(signal["max_drawdown_pct"] or 0), move_pct)

        def reached(level: float) -> bool:
            return price >= level if side == "LONG" else price <= level

        stop_hit = price <= signal["stop"] if side == "LONG" else price >= signal["stop"]
        tp1 = signal["tp1_hit_at"] or (now if reached(signal["tp1"]) else None)
        tp2 = signal["tp2_hit_at"] or (now if reached(signal["tp2"]) else None)
        tp3 = signal["tp3_hit_at"] or (now if reached(signal["tp3"]) else None)
        stop = signal["stop_hit_at"] or (now if stop_hit else None)

        status = "OPEN"
        closed_at = None
        if tp3:
            status, closed_at = "TP3", now
        elif stop:
            status, closed_at = "STOP", now

        self.history.update_progress(
            signal["id"], status=status, current_price=price,
            max_profit_pct=max_profit, max_drawdown_pct=max_drawdown,
            tp1_hit_at=tp1, tp2_hit_at=tp2, tp3_hit_at=tp3,
            stop_hit_at=stop, closed_at=closed_at,
        )

    async def run_forever(self) -> None:
        while True:
            await self.check_once()
            await asyncio.sleep(self.interval_seconds)
