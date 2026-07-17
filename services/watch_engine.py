from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import uuid
from datetime import datetime, timezone

from database.database import (
    acquire_lease,
    connect,
    release_lease,
    runtime_finished,
    runtime_started,
)
from services.analysis_runtime import run_analysis
from services.analyzer import Analyzer
from services.market import Market
from services.probability_engine import ProbabilityEngine
from services.signal_recorder import SignalRecorder


class WatchEngine:
    """Persistently re-analyze user watchlists and emit only material changes."""

    worker_name = "watch_engine"

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
        self.owner_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"

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
    def _load_rows() -> list[dict]:
        with connect() as conn:
            rows = conn.execute(
                """
                SELECT w.telegram_id, w.symbol, w.timeframe,
                       s.snapshot_json, s.updated_at, s.last_notified_at,
                       s.consecutive_errors, u.notifications_enabled
                FROM user_watchlist w
                LEFT JOIN watch_states s
                  ON s.telegram_id=w.telegram_id AND s.symbol=w.symbol AND s.timeframe=w.timeframe
                LEFT JOIN users u ON u.telegram_id=w.telegram_id
                ORDER BY w.telegram_id, w.symbol
                """
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _save_state(telegram_id: int, symbol: str, timeframe: str, snapshot: dict, *, notified: bool = False, signal_id: int | None = None) -> None:
        now = WatchEngine._now()
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO watch_states(
                    telegram_id,symbol,timeframe,snapshot_json,updated_at,last_checked_at,last_notified_at,
                    last_error,consecutive_errors,promoted_signal_id
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(telegram_id,symbol,timeframe) DO UPDATE SET
                    snapshot_json=excluded.snapshot_json,
                    updated_at=excluded.updated_at,
                    last_checked_at=excluded.last_checked_at,
                    last_notified_at=CASE WHEN excluded.last_notified_at IS NOT NULL THEN excluded.last_notified_at ELSE watch_states.last_notified_at END,
                    last_error=NULL,
                    consecutive_errors=0,
                    promoted_signal_id=COALESCE(excluded.promoted_signal_id,watch_states.promoted_signal_id)
                """,
                (telegram_id, symbol, timeframe, json.dumps(snapshot, ensure_ascii=False), now, now, now if notified else None, None, 0, signal_id),
            )

    @staticmethod
    def _save_error(telegram_id: int, symbol: str, timeframe: str, error: str) -> None:
        now = WatchEngine._now()
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO watch_states(telegram_id,symbol,timeframe,snapshot_json,updated_at,last_checked_at,last_error,consecutive_errors)
                VALUES(?,?,?,?,?,?,?,1)
                ON CONFLICT(telegram_id,symbol,timeframe) DO UPDATE SET
                    updated_at=excluded.updated_at,last_checked_at=excluded.last_checked_at,last_error=excluded.last_error,
                    consecutive_errors=watch_states.consecutive_errors+1
                """,
                (telegram_id, symbol, timeframe, "{}", now, now, error[:1000]),
            )

    @staticmethod
    def _add_event(telegram_id: int, symbol: str, timeframe: str, event_type: str, details: dict) -> None:
        with connect() as conn:
            conn.execute(
                "INSERT INTO watch_events(telegram_id,symbol,timeframe,event_type,details_json,created_at) VALUES(?,?,?,?,?,?)",
                (telegram_id, symbol, timeframe, event_type, json.dumps(details, ensure_ascii=False), WatchEngine._now()),
            )

    async def _analyze_one(self, row: dict, semaphore: asyncio.Semaphore) -> dict:
        async with semaphore:
            symbol, timeframe = row["symbol"], row["timeframe"]
            try:
                df = await asyncio.wait_for(self.market.get_klines(symbol, interval=timeframe), timeout=35)
                analysis = await asyncio.wait_for(run_analysis(self.analyzer, df, symbol=symbol, timeframe=timeframe, source="watch_engine"), timeout=45)
                setup_key = self.recorder._setup_key(analysis)
                analysis["timeframe"] = timeframe
                analysis = self.probability.enrich(analysis, symbol=symbol, timeframe=timeframe, setup_key=setup_key)
                current = self._snapshot(analysis)
                signal_id = self.recorder.record(
                    symbol=symbol,
                    timeframe=timeframe,
                    analysis=analysis,
                    owner_telegram_id=row["telegram_id"],
                    notification_chat_id=row["telegram_id"],
                )
                raw_previous = row.get("snapshot_json")
                previous = json.loads(raw_previous) if raw_previous else None
                changes = self._material_changes(previous, current) if previous else []
                self._save_state(row["telegram_id"], symbol, timeframe, current, notified=bool(changes), signal_id=signal_id)
                if signal_id:
                    self._add_event(row["telegram_id"], symbol, timeframe, "PROMOTED_TO_SIGNAL", {"signal_id": signal_id})
                if changes:
                    self._add_event(row["telegram_id"], symbol, timeframe, "MATERIAL_CHANGE", {"changes": changes, "snapshot": current})
                if changes and self.bot and bool(row.get("notifications_enabled", 1)):
                    lines = [
                        f"🔔 <b>{symbol} · {timeframe.upper()} WATCH UPDATE</b>", "",
                        *[f"• {change}" for change in changes], "",
                        f"Bias: {current.get('market_bias')}",
                        f"Recommendation: {current.get('recommendation')}",
                        f"Direction / Ready: {current['direction_score']:.1f} / {current['readiness']:.1f}",
                        f"Price: <code>{current['price']}</code>",
                    ]
                    await self.bot.send_message(row["telegram_id"], "\n".join(lines), parse_mode="HTML")
                return {"ok": True, "signal_id": signal_id, "notified": bool(changes)}
            except Exception as exc:
                logging.exception("Watch engine failed for %s %s", symbol, timeframe)
                self._save_error(row["telegram_id"], symbol, timeframe, str(exc))
                return {"ok": False, "error": str(exc)}

    async def check_once(self) -> dict[str, int | bool]:
        lease_ttl = max(self.interval_seconds * 2, 180)
        if not acquire_lease(self.worker_name, self.owner_id, lease_ttl):
            return {"skipped": True, "processed": 0, "errors": 0, "notifications": 0, "promoted": 0}
        runtime_started(self.worker_name)
        try:
            rows = self._load_rows()
            if not rows:
                runtime_finished(self.worker_name, processed=0, errors=0, details={"watchlist": 0})
                return {"skipped": False, "processed": 0, "errors": 0, "notifications": 0, "promoted": 0}
            semaphore = asyncio.Semaphore(self.concurrency)
            results = await asyncio.gather(*(self._analyze_one(row, semaphore) for row in rows))
            errors = sum(1 for item in results if not item.get("ok"))
            notifications = sum(1 for item in results if item.get("notified"))
            promoted = sum(1 for item in results if item.get("signal_id"))
            details = {"watchlist": len(rows), "notifications": notifications, "promoted": promoted}
            runtime_finished(self.worker_name, processed=len(rows), errors=errors, details=details)
            return {"skipped": False, "processed": len(rows), "errors": errors, "notifications": notifications, "promoted": promoted}
        except Exception as exc:
            runtime_finished(self.worker_name, processed=0, errors=1, error=str(exc))
            raise
        finally:
            release_lease(self.worker_name, self.owner_id)

    async def run_forever(self) -> None:
        logging.info("WatchEngine started: interval=%ss, concurrency=%s", self.interval_seconds, self.concurrency)
        while not self._stop.is_set():
            try:
                await self.check_once()
            except Exception:
                logging.exception("WatchEngine cycle failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                pass
        logging.info("WatchEngine stopped")
