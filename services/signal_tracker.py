from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import uuid
from datetime import datetime, timedelta, timezone

from aiogram import Bot

from database.database import acquire_lease, release_lease, runtime_finished, runtime_started
from database.signal_history import SignalHistory
from services.market import Market
from services.notifier import Notifier
from services.trade_intelligence import TradeIntelligenceEngine


class SignalTracker:
    def __init__(self, interval_seconds: int = 60, bot: Bot | None = None):
        self.interval_seconds = max(30, interval_seconds)
        self.history = SignalHistory()
        self.market = Market()
        self.notifier = Notifier(bot)
        self.intelligence = TradeIntelligenceEngine()
        self.owner_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self.auto_break_even = os.getenv("AUTO_BREAK_EVEN_AFTER_TP1", "true").lower() in {"1", "true", "yes", "on"}
        self.progress_interval = max(300, int(os.getenv("TRADE_PROGRESS_INTERVAL", "900")))
        self.progress_step = max(10, int(os.getenv("TRADE_PROGRESS_STEP", "20")))
        self._stop = asyncio.Event()

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

    @staticmethod
    def _r_multiple(side: str, entry: float, stop: float, price: float) -> float:
        risk = abs(entry - stop)
        if risk <= 0:
            return 0.0
        move = (price - entry) if side == "LONG" else (entry - price)
        return move / risk

    @staticmethod
    def _target_progress(side: str, entry: float, target: float, price: float) -> float:
        distance = abs(target - entry)
        if distance <= 0:
            return 0.0
        move = (price - entry) if side == "LONG" else (entry - price)
        return max(0.0, min(100.0, move / distance * 100))

    async def check_once(self) -> dict[str, int | bool]:
        if not acquire_lease("signal_tracker", self.owner_id, max(self.interval_seconds * 2, 120)):
            return {"skipped": True, "processed": 0, "errors": 0}
        runtime_started("signal_tracker")
        processed = errors = 0
        try:
            for signal in self.history.get_open():
                processed += 1
                try:
                    df = await asyncio.wait_for(self.market.get_klines(signal["symbol"], "1m", 6), timeout=25)
                    price = float(df["close"].iloc[-1])
                    await self._update(signal, price, df)
                except Exception:
                    errors += 1
                    logging.exception("Failed to update signal %s", signal["id"])
            runtime_finished("signal_tracker", processed=processed, errors=errors)
            return {"skipped": False, "processed": processed, "errors": errors}
        except Exception as exc:
            runtime_finished("signal_tracker", processed=processed, errors=errors + 1, error=str(exc))
            raise
        finally:
            release_lease("signal_tracker", self.owner_id)

    async def _transition(self, signal: dict, event: str, price: float, **fields) -> None:
        previous = signal["status"]
        fields["status"] = event
        signal.update(fields)
        signal["status"] = event
        self.history.update_lifecycle(signal["id"], **fields)
        self.history.add_event(signal["id"], event, price, {"from": previous, **{k: v for k, v in fields.items() if k != "status"}})
        if signal.get("last_notified_status") != event:
            await self.notifier.lifecycle(signal, event, price)
            self.history.update_lifecycle(signal["id"], last_notified_status=event)
            signal["last_notified_status"] = event

    async def _maybe_progress(self, signal: dict, price: float, now: str) -> None:
        if signal["status"] not in {"ACTIVE", "TP1", "TP2"}:
            return
        progress = self._target_progress(signal["side"], float(signal["entry"]), float(signal["tp1"]), price)
        bucket = int(progress // self.progress_step)
        last_bucket = int(signal.get("last_progress_bucket") if signal.get("last_progress_bucket") is not None else -1)
        due = False
        last_at = signal.get("last_progress_notified_at")
        if last_at:
            try:
                due = (datetime.now(timezone.utc) - datetime.fromisoformat(last_at)).total_seconds() >= self.progress_interval
            except (ValueError, TypeError):
                due = True
        else:
            due = True
        if bucket > last_bucket or due:
            await self.notifier.progress(signal, price)
            self.history.update_lifecycle(
                signal["id"],
                last_progress_notified_at=now,
                last_progress_bucket=bucket,
            )
            signal["last_progress_notified_at"] = now
            signal["last_progress_bucket"] = bucket

    async def _update(self, signal: dict, price: float, df) -> None:
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        side = signal["side"]
        status = signal["status"]
        entry = float(signal["entry"])
        initial_stop = float(signal["stop"])
        effective_stop = float(signal.get("effective_stop") or initial_stop)

        if status in {"WATCHING", "TRIGGERED"}:
            pre_move = ((price - entry) / entry * 100) * (1 if side == "LONG" else -1) if entry else 0.0
            pre_profit = max(float(signal.get("pre_activation_max_profit_pct") or 0), pre_move)
            pre_drawdown = min(float(signal.get("pre_activation_max_drawdown_pct") or 0), pre_move)
            pending_fields = {
                "current_price": price,
                "pre_activation_max_profit_pct": pre_profit,
                "pre_activation_max_drawdown_pct": pre_drawdown,
            }
            self.history.update_lifecycle(signal["id"], **pending_fields)
            signal.update(pending_fields)

        expires_at = signal.get("expires_at")
        if status in {"WATCHING", "TRIGGERED"} and expires_at:
            try:
                if now_dt >= datetime.fromisoformat(expires_at):
                    await self._transition(signal, "EXPIRED", price, closed_at=now, exit_price=price, result="EXPIRED")
                    return
            except ValueError:
                pass

        if status in {"WATCHING", "TRIGGERED"} and self._stop_hit(side, price, initial_stop):
            await self._transition(
                signal,
                "INVALIDATED",
                price,
                invalidated_at=now,
                closed_at=now,
                exit_price=price,
                result="INVALIDATED_BEFORE_ENTRY",
            )
            return

        zone_low = signal.get("preferred_entry_low")
        zone_high = signal.get("preferred_entry_high")
        if status == "WATCHING":
            if self._in_zone(price, zone_low, zone_high):
                await self._transition(signal, "TRIGGERED", price, triggered_at=now, current_price=price)
                return
            no_zone = zone_low is None or zone_high is None
            crossed_entry = price >= entry if side == "LONG" else price <= entry
            if no_zone and crossed_entry:
                await self._transition(
                    signal,
                    "ACTIVE",
                    price,
                    activated_at=now,
                    current_price=price,
                    highest_price=price,
                    lowest_price=price,
                    max_profit_pct=0.0,
                    max_drawdown_pct=0.0,
                    effective_stop=initial_stop,
                    plan_locked_at=now,
                )
                return

        if status == "TRIGGERED":
            candle = df.iloc[-2] if len(df) >= 2 else df.iloc[-1]
            if self._directional_candle(side, candle):
                await self._transition(
                    signal,
                    "ACTIVE",
                    price,
                    activated_at=now,
                    current_price=price,
                    highest_price=price,
                    lowest_price=price,
                    max_profit_pct=0.0,
                    max_drawdown_pct=0.0,
                    effective_stop=initial_stop,
                    plan_locked_at=now,
                )
                return

        if signal["status"] not in {"ACTIVE", "TP1", "TP2"}:
            self.history.update_lifecycle(signal["id"], current_price=price)
            return

        move_pct = ((price - entry) / entry * 100) * (1 if side == "LONG" else -1)
        max_profit = max(float(signal.get("max_profit_pct") or 0), move_pct)
        max_drawdown = min(float(signal.get("max_drawdown_pct") or 0), move_pct)
        highest = max(float(signal.get("highest_price") or price), price)
        lowest = min(float(signal.get("lowest_price") or price), price)
        common = {
            "current_price": price,
            "max_profit_pct": max_profit,
            "max_drawdown_pct": max_drawdown,
            "highest_price": highest,
            "lowest_price": lowest,
        }
        signal.update(common)

        snapshot = self.intelligence.evaluate(signal, price, df)
        intelligence_payload = snapshot.to_dict()
        intelligence_fields = {
            "previous_confidence": float(signal.get("dynamic_confidence") or signal.get("confidence") or snapshot.confidence),
            "dynamic_confidence": snapshot.confidence,
            "trade_health": snapshot.health,
            "health_score": snapshot.health_score,
            "intelligence_json": json.dumps(intelligence_payload, ensure_ascii=False),
            "last_risk_used": snapshot.risk_used,
            "last_mfe_giveback": snapshot.mfe_giveback,
        }
        signal.update(intelligence_fields)
        common.update(intelligence_fields)

        if snapshot.alert_reasons:
            signature = "|".join(snapshot.alert_reasons)
            last_alert_at = signal.get("last_intelligence_notified_at")
            cooldown_ok = True
            if last_alert_at:
                try:
                    parsed = datetime.fromisoformat(str(last_alert_at))
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    cooldown_ok = datetime.now(timezone.utc) - parsed >= timedelta(minutes=15)
                except (TypeError, ValueError):
                    cooldown_ok = True
            critical = snapshot.health == "🔴 AT RISK" or any("90%" in reason for reason in snapshot.alert_reasons)
            if signature != str(signal.get("last_alert_signature") or "") and (cooldown_ok or critical):
                await self.notifier.smart_alert(signal, price, snapshot.alert_reasons)
                alerted_at = now
                alert_fields = {
                    "last_intelligence_notified_at": alerted_at,
                    "last_alert_signature": signature,
                }
                signal.update(alert_fields)
                common.update(alert_fields)
                self.history.add_event(
                    signal["id"],
                    "INTELLIGENCE_ALERT",
                    price,
                    {
                        "reasons": snapshot.alert_reasons,
                        "confidence": snapshot.confidence,
                        "health": snapshot.health,
                    },
                )

        effective_stop = float(signal.get("effective_stop") or initial_stop)
        if self._stop_hit(side, price, effective_stop):
            is_be = bool(signal.get("break_even_at")) and abs(effective_stop - entry) <= max(abs(entry) * 1e-8, 1e-12)
            event = "BREAKEVEN" if is_be else "STOP"
            realized_r = 0.0 if is_be else self._r_multiple(side, entry, initial_stop, price)
            await self._transition(
                signal,
                event,
                price,
                stop_hit_at=now,
                closed_at=now,
                exit_price=price,
                realized_r=realized_r,
                result="BREAKEVEN_AFTER_TP1" if is_be else "STOP",
                **common,
            )
            return

        if not signal.get("tp3_hit_at") and self._reached(side, price, float(signal["tp3"])):
            common.update({
                "tp1_hit_at": signal.get("tp1_hit_at") or now,
                "tp2_hit_at": signal.get("tp2_hit_at") or now,
                "tp3_hit_at": now,
                "closed_at": now,
                "exit_price": price,
                "realized_r": self._r_multiple(side, entry, initial_stop, float(signal["tp3"])),
                "result": "TP3",
            })
            await self._transition(signal, "TP3", price, **common)
            return

        if not signal.get("tp2_hit_at") and self._reached(side, price, float(signal["tp2"])):
            common.update({
                "tp1_hit_at": signal.get("tp1_hit_at") or now,
                "tp2_hit_at": now,
                "realized_r": self._r_multiple(side, entry, initial_stop, float(signal["tp2"])),
                "result": "TP2_OPEN",
            })
            await self._transition(signal, "TP2", price, **common)
            return

        if not signal.get("tp1_hit_at") and self._reached(side, price, float(signal["tp1"])):
            common.update({
                "tp1_hit_at": now,
                "realized_r": self._r_multiple(side, entry, initial_stop, float(signal["tp1"])),
                "result": "TP1_OPEN",
            })
            if self.auto_break_even:
                common.update({"effective_stop": entry, "break_even_at": now})
            await self._transition(signal, "TP1", price, **common)
            if self.auto_break_even:
                self.history.add_event(signal["id"], "BREAK_EVEN_SET", entry, {"old_stop": initial_stop, "new_stop": entry})
            return

        self.history.update_lifecycle(signal["id"], **common)
        await self._maybe_progress(signal, price, now)

    def stop(self) -> None:
        self._stop.set()

    async def run_forever(self) -> None:
        logging.info("SignalTracker started: interval=%ss", self.interval_seconds)
        while not self._stop.is_set():
            await self.check_once()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                pass
        logging.info("SignalTracker stopped")
