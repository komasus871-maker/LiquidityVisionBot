from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

from database.database import connect
from services.analyzer import Analyzer
from services.market import Market
from services.probability_engine import ProbabilityEngine
from services.signal_recorder import SignalRecorder
from services.analysis_runtime import run_analysis


class WatchEngine:
    """Autonomously re-analyze personal watchlists and notify only on material changes."""

    def __init__(self, bot=None, interval_seconds: int | None = None):
        self.bot = bot
        self.interval_seconds = max(60, interval_seconds or int(os.getenv("WATCHLIST_CHECK_INTERVAL", "300")))
        self.concurrency = max(1, int(os.getenv("WATCHLIST_MONITOR_CONCURRENCY", "4")))
        self.score_delta = float(os.getenv("WATCHLIST_SCORE_DELTA", "12"))
        self.readiness_delta = float(os.getenv("WATCHLIST_READINESS_DELTA", "12"))
        self.market = Market()
        self.analyzer = Analyzer()
        self.probability = ProbabilityEngine()
        self.recorder = SignalRecorder()
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _snapshot(analysis: dict) -> dict:
        return {
            "price": float(analysis.get("price") or 0),
            "direction": analysis.get("direction"),
            "market_bias": analysis.get("market_bias"),
            "execution_status": analysis.get("execution_status"),
            "recommendation": analysis.get("recommendation"),
            "direction_score": float(analysis.get("direction_score") or 0),
            "readiness": float(analysis.get("execution_readiness") or 0),
            "bos": analysis.get("bos"),
            "choch": analysis.get("choch"),
            "preferred_entry_low": analysis.get("preferred_entry_low"),
            "preferred_entry_high": analysis.get("preferred_entry_high"),
            "rr": float(analysis.get("rr") or 0),
        }

    @staticmethod
    def _in_zone(price: float, low, high) -> bool:
        if low is None or high is None:
            return False
        return min(float(low), float(high)) <= price <= max(float(low), float(high))

    def _material_changes(self, previous: dict, current: dict) -> list[str]:
        changes: list[str] = []
        if previous.get("execution_status") != current.get("execution_status"):
            changes.append(f"Status: {previous.get('execution_status', '—')} → {current.get('execution_status', '—')}")
        if previous.get("direction") != current.get("direction"):
            changes.append(f"Direction: {previous.get('direction', '—')} → {current.get('direction', '—')}")
        if abs(current["direction_score"] - float(previous.get("direction_score") or 0)) >= self.score_delta:
            changes.append(f"Direction score: {float(previous.get('direction_score') or 0):.1f} → {current['direction_score']:.1f}")
        if abs(current["readiness"] - float(previous.get("readiness") or 0)) >= self.readiness_delta:
            changes.append(f"Readiness: {float(previous.get('readiness') or 0):.1f} → {current['readiness']:.1f}")
        if previous.get("bos") != current.get("bos") and "No BOS" not in str(current.get("bos")):
            changes.append(f"BOS: {current.get('bos')}")
        if previous.get("choch") != current.get("choch") and "No CHOCH" not in str(current.get("choch")):
            changes.append(f"CHOCH: {current.get('choch')}")

        was_in_zone = self._in_zone(float(previous.get("price") or 0), previous.get("preferred_entry_low"), previous.get("preferred_entry_high"))
        is_in_zone = self._in_zone(current["price"], current.get("preferred_entry_low"), current.get("preferred_entry_high"))
        if is_in_zone and not was_in_zone:
            changes.append("Price entered the preferred entry zone")
        return changes

    @staticmethod
    def _load_rows():
        with connect() as conn:
            return [dict(row) for row in conn.execute(
                """
                SELECT w.telegram_id, w.symbol, w.timeframe,
                       s.snapshot_json, s.updated_at, s.last_notified_at
                FROM user_watchlist w
                LEFT JOIN watch_states s
                  ON s.telegram_id=w.telegram_id AND s.symbol=w.symbol AND s.timeframe=w.timeframe
                ORDER BY w.telegram_id, w.symbol
                """
            ).fetchall()]

    @staticmethod
    def _save_state(telegram_id: int, symbol: str, timeframe: str, snapshot: dict, notified: bool = False) -> None:
        now = WatchEngine._now()
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO watch_states(telegram_id, symbol, timeframe, snapshot_json, updated_at, last_notified_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(telegram_id, symbol, timeframe) DO UPDATE SET
                    snapshot_json=excluded.snapshot_json,
                    updated_at=excluded.updated_at,
                    last_notified_at=CASE WHEN excluded.last_notified_at IS NOT NULL
                        THEN excluded.last_notified_at ELSE watch_states.last_notified_at END
                """,
                (telegram_id, symbol, timeframe, json.dumps(snapshot, ensure_ascii=False), now, now if notified else None),
            )

    async def _analyze_one(self, row: dict, semaphore: asyncio.Semaphore) -> None:
        async with semaphore:
            symbol, timeframe = row["symbol"], row["timeframe"]
            try:
                df = await self.market.get_klines(symbol, interval=timeframe)
                analysis = await run_analysis(self.analyzer, df)
                setup_key = self.recorder._setup_key(analysis)
                analysis["timeframe"] = timeframe
                analysis = self.probability.enrich(analysis, symbol=symbol, timeframe=timeframe, setup_key=setup_key)
                current = self._snapshot(analysis)
                raw_previous = row.get("snapshot_json")
                if not raw_previous:
                    self._save_state(row["telegram_id"], symbol, timeframe, current)
                    return
                previous = json.loads(raw_previous)
                changes = self._material_changes(previous, current)
                notified = bool(changes)
                self._save_state(row["telegram_id"], symbol, timeframe, current, notified=notified)
                if not changes or not self.bot:
                    return

                # Promote meaningful watchlist changes into lifecycle tracking.
                if current["execution_status"] in {"🟢 READY", "🎯 WAIT FOR PULLBACK", "🟡 WAIT FOR TRIGGER"}:
                    self.recorder.record(
                        symbol=symbol,
                        timeframe=timeframe,
                        analysis=analysis,
                        owner_telegram_id=row["telegram_id"],
                        notification_chat_id=row["telegram_id"],
                    )

                lines = [
                    f"🔔 <b>{symbol} · {timeframe.upper()} WATCH UPDATE</b>",
                    "",
                    *[f"• {change}" for change in changes],
                    "",
                    f"Bias: {current.get('market_bias')}",
                    f"Recommendation: {current.get('recommendation')}",
                    f"Direction / Ready: {current['direction_score']:.1f} / {current['readiness']:.1f}",
                    f"Price: <code>{current['price']}</code>",
                ]
                await self.bot.send_message(row["telegram_id"], "\n".join(lines), parse_mode="HTML")
            except Exception as exc:
                logging.warning("Watch engine failed for %s %s: %s", symbol, timeframe, exc)

    async def check_once(self) -> None:
        rows = self._load_rows()
        if not rows:
            return
        semaphore = asyncio.Semaphore(self.concurrency)
        await asyncio.gather(*(self._analyze_one(row, semaphore) for row in rows), return_exceptions=True)

    async def run_forever(self) -> None:
        logging.info("WatchEngine started: interval=%ss, concurrency=%s", self.interval_seconds, self.concurrency)
        while not self._stop.is_set():
            await self.check_once()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                pass
        logging.info("WatchEngine stopped")
