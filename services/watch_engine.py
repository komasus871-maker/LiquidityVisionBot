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
from services.intelligence_alerts import IntelligenceAlertService
from services.localization import LocalizationService


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
        self.alerts = IntelligenceAlertService(
            debounce_minutes=int(os.getenv("ALERT_DEBOUNCE_MINUTES", "30")),
        )
        self.i18n = LocalizationService()
        self._stop = asyncio.Event()
        self.owner_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"

    def stop(self) -> None:
        self._stop.set()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _snapshot(analysis: dict) -> dict:
        intelligence = analysis.get("market_intelligence") or {}
        quality = intelligence.get("signal_quality_v4") or intelligence.get("signal_quality_v3") or {}
        readiness = intelligence.get("entry_readiness") or {}
        fusion = intelligence.get("strategy_fusion_v2") or {}
        primary = fusion.get("primary") or {}
        tied = fusion.get("tied_strategies") or []
        regime = intelligence.get("market_regime_v2") or {}
        microstructure = intelligence.get("microstructure") or {}
        derivatives = intelligence.get("funding_open_interest") or {}
        return {
            "price": float(analysis.get("price") or 0),
            "direction": analysis.get("direction"),
            "market_bias": analysis.get("market_bias"),
            "execution_status": analysis.get("execution_status"),
            "recommendation": analysis.get("recommendation"),
            "direction_score": float(analysis.get("direction_score") or 0),
            "readiness": (float(readiness["score"]) if readiness.get("score") is not None else None),
            "readiness_state": readiness.get("state") or analysis.get("execution_status"),
            "quality": (float(quality["overall_quality"]) if quality.get("overall_quality") is not None else None),
            "quality_state": (quality.get("evaluation_state") or
                              ("LEGACY_SNAPSHOT" if intelligence else "NOT_EVALUATED")),
            "setup_quality": quality.get("setup_quality"),
            "data_confidence": quality.get("data_confidence"),
            "strategy": (" / ".join(str(item) for item in tied[:2]) if tied else
                         primary.get("strategy") or analysis.get("setup_type") or "UNCLASSIFIED"),
            "strategy_fit": float(primary.get("suitability") or 0),
            "regime": regime.get("phase") or (intelligence.get("market_story") or {}).get("state"),
            "microstructure_quality": (float(microstructure["microstructure_quality"])
                                       if microstructure.get("microstructure_quality") is not None else None),
            "microstructure_labels": sorted(microstructure.get("behavior_labels") or []),
            "funding_percentile": (float(derivatives["funding_percentile"])
                                   if derivatives.get("funding_percentile") is not None else None),
            "oi_acceleration": derivatives.get("oi_acceleration"),
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
        if (current.get("direction_score") is not None and previous.get("direction_score") is not None
                and abs(float(current["direction_score"]) - float(previous["direction_score"])) >= self.score_delta):
            changes.append(f"Direction score: {float(previous.get('direction_score') or 0):.1f} → {current['direction_score']:.1f}")
        if (current.get("readiness") is not None and previous.get("readiness") is not None
                and abs(float(current["readiness"]) - float(previous["readiness"])) >= self.readiness_delta):
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

    def _alert_candidates(self, previous: dict, current: dict) -> list[dict]:
        candidates: list[dict] = []
        def changed(field: str, alert_type: str, label: str, *, threshold: float | None = None) -> None:
            before, after = previous.get(field), current.get(field)
            if threshold is not None:
                if before is None or after is None:
                    return
                if abs(float(after or 0) - float(before or 0)) < threshold:
                    return
            elif before == after:
                return
            candidates.append({"type": alert_type, "identity": f"{field}:{after}",
                               "text": f"{label}: {before or '—'} → {after or '—'}",
                               "before": before, "after": after})
        changed("execution_status", "STATUS_CHANGE", "Status")
        changed("direction", "DIRECTION_CHANGE", "Direction")
        changed("readiness", "READINESS_CHANGE", "Readiness", threshold=self.readiness_delta)
        changed("quality", "QUALITY_CHANGE", "Quality", threshold=self.score_delta)
        changed("readiness_state", "READINESS_CHANGE", "Readiness state")
        changed("strategy", "STRATEGY_CHANGE", "Primary strategy")
        changed("regime", "REGIME_CHANGE", "Market regime")
        changed("microstructure_quality", "MICROSTRUCTURE_CHANGE", "Microstructure quality", threshold=self.score_delta)
        previous_labels = set(previous.get("microstructure_labels") or [])
        for label in sorted(set(current.get("microstructure_labels") or []) - previous_labels):
            if label in {"MICROSTRUCTURE_NEUTRAL", "INSUFFICIENT_HISTORY"}:
                continue
            alert_type = ("WALL_REMOVED" if "REMOVED" in label else
                          "WALL_REPLENISHED" if "REPLENISH" in label else
                          "LIQUIDITY_SWEEP" if "SWEEP" in label else
                          "ORDER_BOOK_WALL_APPEARS" if "WALL" in label else
                          "MICROSTRUCTURE_CHANGE")
            candidates.append({"type": alert_type, "identity": f"microstructure:{label}",
                               "text": f"Microstructure event: {label}",
                               "before": None, "after": label})
        percentile = current.get("funding_percentile")
        previous_percentile = previous.get("funding_percentile")
        current_extreme = ("HIGH" if percentile is not None and float(percentile) >= 90 else
                           "LOW" if percentile is not None and float(percentile) <= 10 else None)
        previous_extreme = ("HIGH" if previous_percentile is not None and float(previous_percentile) >= 90 else
                            "LOW" if previous_percentile is not None and float(previous_percentile) <= 10 else None)
        if current_extreme and current_extreme != previous_extreme:
            candidates.append({"type": "FUNDING_EXTREME", "identity": f"funding:{current_extreme}",
                               "text": f"Funding percentile entered {current_extreme} extreme",
                               "before": previous_percentile, "after": percentile})
        if (current.get("oi_acceleration") == "ACCELERATING"
                and previous.get("oi_acceleration") != "ACCELERATING"):
            candidates.append({"type": "OI_ACCELERATION", "identity": "oi:accelerating",
                               "text": "Open interest is accelerating",
                               "before": previous.get("oi_acceleration"), "after": "ACCELERATING"})
        if previous.get("bos") != current.get("bos") and "No BOS" not in str(current.get("bos")):
            candidates.append({"type": "STRUCTURE_BREAK", "identity": f"bos:{current.get('bos')}",
                               "text": f"BOS: {current.get('bos')}"})
        was_in_zone = self._in_zone(float(previous.get("price") or 0), previous.get("preferred_entry_low"), previous.get("preferred_entry_high"))
        is_in_zone = self._in_zone(current["price"], current.get("preferred_entry_low"), current.get("preferred_entry_high"))
        if is_in_zone and not was_in_zone:
            candidates.append({"type": "ENTRY_ZONE", "identity": "entry-zone:entered",
                               "text": "Price entered the preferred entry zone"})
        return candidates

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
                candidates = self._alert_candidates(previous, current) if previous else []
                eligible = []
                for candidate in candidates:
                    decision = self.alerts.evaluate(
                        row["telegram_id"], symbol=symbol, timeframe=timeframe,
                        alert_type=candidate["type"], state_identity=candidate["identity"],
                        details={"change": candidate, "snapshot": current, "signal_id": signal_id},
                    )
                    if decision["status"] == "ELIGIBLE":
                        eligible.append((candidate, decision))
                notified = False
                if signal_id:
                    self._add_event(row["telegram_id"], symbol, timeframe, "PROMOTED_TO_SIGNAL", {"signal_id": signal_id})
                if changes:
                    self._add_event(row["telegram_id"], symbol, timeframe, "MATERIAL_CHANGE", {"changes": changes, "snapshot": current})
                if eligible and self.bot and bool(row.get("notifications_enabled", 1)):
                    language = self.i18n.language(row["telegram_id"])
                    quality_text = (f"{current['quality']:.1f}" if current.get("quality") is not None else "—")
                    readiness_text = (f"{current['readiness']:.1f}" if current.get("readiness") is not None else "—")
                    lines = [
                        f"🔔 <b>{self.i18n.market_token(symbol, language=language)} · "
                        f"{self.i18n.market_token(timeframe.upper(), language=language)} WATCH UPDATE</b>", "",
                        *[f"• {candidate['text']}" for candidate, _ in eligible], "",
                        f"Bias: {current.get('market_bias')}",
                        f"Recommendation: {current.get('recommendation')}",
                        f"Quality / Readiness: {quality_text} / {readiness_text}",
                        f"Strategy: {current['strategy']} · Regime: {current['regime']}",
                        f"Price: <code>{current['price']}</code>",
                    ]
                    try:
                        await self.bot.send_message(row["telegram_id"], "\n".join(lines), parse_mode="HTML")
                    except Exception:
                        for _, decision in eligible:
                            self.alerts.mark_delivery_failed(decision["alert_key"])
                        logging.exception("Watch alert delivery failed for %s %s", symbol, timeframe)
                    else:
                        notified = True
                        for _, decision in eligible:
                            self.alerts.mark_delivered(decision["alert_key"])
                self._save_state(row["telegram_id"], symbol, timeframe, current, notified=notified, signal_id=signal_id)
                return {"ok": True, "signal_id": signal_id, "notified": notified,
                        "eligible_alerts": len(eligible)}
            except Exception as exc:
                logging.exception("Watch engine failed for %s %s", symbol, timeframe)
                self._save_error(row["telegram_id"], symbol, timeframe, str(exc))
                return {"ok": False, "error": str(exc)}

    async def check_once(self) -> dict[str, int | bool]:
        lease_ttl = max(self.interval_seconds * 2, 180)
        if not acquire_lease(self.worker_name, self.owner_id, lease_ttl):
            runtime_finished(self.worker_name, processed=0, errors=0, details={"skipped": "lease_busy"})
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
                cycle_timeout = int(os.getenv("WATCH_ENGINE_CYCLE_TIMEOUT", str(max(180, self.interval_seconds * 2))))
                await asyncio.wait_for(self.check_once(), timeout=cycle_timeout)
            except asyncio.TimeoutError:
                logging.error("WatchEngine cycle timed out")
                runtime_finished(self.worker_name, processed=0, errors=1, error="cycle timeout")
            except Exception:
                logging.exception("WatchEngine cycle failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                pass
        logging.info("WatchEngine stopped")
