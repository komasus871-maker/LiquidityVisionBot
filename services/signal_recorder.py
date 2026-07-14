from typing import Any
import re
from database.signal_history import SignalHistory
from services.premium import PremiumService
from database.observation_history import ObservationHistory
from database.candidate_history import CandidateHistory
from database.database import connect


class SignalRecorder:
    def __init__(self, history: SignalHistory | None = None):
        self.history = history or SignalHistory()
        self.premium = PremiumService()
        self.observations = ObservationHistory()
        self.candidates = CandidateHistory()

    @staticmethod
    def _setup_key(analysis: dict[str, Any]) -> str:
        parts = [
            analysis.get("structure"), analysis.get("choch"), analysis.get("sweep"),
            analysis.get("order_block"), analysis.get("premium", {}).get("zone"),
        ]
        normalized = []
        for part in parts:
            if not part:
                continue
            text = str(part).replace("✅", "").strip()
            text = re.sub(r"\([^)]*\)", "", text)
            text = re.sub(r"[-+]?\d+(?:\.\d+)?", "", text)
            text = re.sub(r"\s+", " ", text).strip(" -|")
            normalized.append(text)
        return " | ".join(normalized)

    @staticmethod
    def _live_symbol_trades(owner_telegram_id: int | None, symbol: str) -> list[dict[str, Any]]:
        if owner_telegram_id is None:
            return []
        with connect() as conn:
            rows = conn.execute(
                """SELECT * FROM signals
                   WHERE owner_telegram_id=? AND symbol=?
                     AND status IN ('ACTIVE','TP1','TP2')
                   ORDER BY id ASC""",
                (owner_telegram_id, symbol.upper()),
            ).fetchall()
        return [dict(row) for row in rows]

    def record(self, *, symbol: str, timeframe: str, analysis: dict[str, Any], owner_telegram_id: int | None = None,
               notification_chat_id: int | None = None, min_confidence: float = 54) -> int | None:
        if not analysis.get("plan_valid", True):
            return None
        if not analysis.get("decision_gate_passed", True):
            return None
        side = str(analysis.get("direction") or "").upper()
        entry, stop = float(analysis.get("entry") or 0), float(analysis.get("stop") or 0)
        tp1, tp2, tp3 = (float(analysis.get(k) or 0) for k in ("tp1", "tp2", "tp3"))
        valid_geometry = (side == "LONG" and stop < entry < tp1 < tp2 < tp3) or (
            side == "SHORT" and tp3 < tp2 < tp1 < entry < stop
        )
        if not valid_geometry:
            return None
        setup_key = self._setup_key(analysis)
        observation_id = self.observations.save_or_update(
            owner_telegram_id=owner_telegram_id, notification_chat_id=notification_chat_id,
            symbol=symbol, timeframe=timeframe, analysis=analysis, setup_key=setup_key,
        )
        analysis["observation_id"] = observation_id

        executable = {"🟢 READY", "🟡 WAIT FOR TRIGGER", "🎯 WAIT FOR PULLBACK", "🔄 REVERSAL WATCH"}
        status_name = analysis.get("execution_status")
        status_minimums = {
            "🟢 READY": 58,
            "🟡 WAIT FOR TRIGGER": 56,
            "🎯 WAIT FOR PULLBACK": 50,
            "🔄 REVERSAL WATCH": 52,
        }
        if status_name not in executable:
            return None
        required = max(min_confidence if status_name == "🟢 READY" else 0, status_minimums[status_name])
        if analysis.get("confidence", 0) < required or analysis.get("rr", 0) < 1.35:
            return None

        if owner_telegram_id is not None:
            stats = self.history.get_stats(owner_telegram_id)
            open_count = (stats.get("watching_count") or 0) + (stats.get("triggered_count") or 0) + (stats.get("active_count") or 0)
            limit = 200 if self.premium.status(owner_telegram_id)["active"] else 20
            if open_count >= limit:
                return None

        status = "ACTIVE" if status_name == "🟢 READY" else "WATCHING"
        live_symbol = self._live_symbol_trades(owner_telegram_id, symbol)
        if status == "ACTIVE" and live_symbol:
            # Never create a second live position on the same instrument across
            # timeframes. Preserve the analysis as an observation only.
            analysis["portfolio_conflict"] = {
                "blocked": True,
                "signal_ids": [int(x.get("id") or 0) for x in live_symbol],
                "sides": [str(x.get("side") or "") for x in live_symbol],
                "timeframes": [str(x.get("timeframe") or "") for x in live_symbol],
            }
            return None
        features = {key: analysis.get(key) for key in (
            "trend", "structure", "bos", "choch", "liquidity", "sweep", "order_block", "breaker",
            "mitigation", "fvg", "premium", "volume", "displacement", "rsi", "macd", "ema50", "ema200",
            "market_bias", "execution_status", "triggers", "direction_score", "entry_quality", "risk_quality",
            "execution_readiness", "opportunity_category", "direction_breakdown",
            "strongest_drivers", "biggest_blockers", "ai_grade", "execution_bias",
            "final_verdict", "score_components", "global_context", "entry_reasons", "expected_path",
            "decision_action", "trade_quality_stars"
        )}
        payload = {
            "owner_telegram_id": owner_telegram_id, "notification_chat_id": notification_chat_id,
            "symbol": symbol.upper(), "timeframe": timeframe, "side": analysis.get("direction", "LONG"),
            "entry": analysis["entry"], "preferred_entry_low": analysis.get("preferred_entry_low"),
            "preferred_entry_high": analysis.get("preferred_entry_high"), "stop": analysis["stop"],
            "tp1": analysis["tp1"], "tp2": analysis["tp2"], "tp3": analysis["tp3"], "rr": analysis["rr"],
            "confidence": analysis["confidence"], "bull_score": analysis["bull_score"], "bear_score": analysis["bear_score"],
            "recommendation": analysis["recommendation"], "setup_key": setup_key,
            "features": features, "reasons": analysis["reasons"], "status": status,
        }
        # One market may have only one open Trade. Opposite analysis becomes a Candidate
        # while a live trade exists; it never creates a conflicting second signal.
        open_market = self.history.get_open_market(owner_telegram_id, payload["symbol"], timeframe)
        live_trade = next((x for x in open_market if x.get("status") in {"ACTIVE", "TP1", "TP2"}), None)
        same_side = [x for x in open_market if x.get("side") == payload["side"]]
        opposite = [x for x in open_market if x.get("side") != payload["side"]]

        if live_trade:
            if live_trade.get("side") == payload["side"]:
                # Refresh metadata only; the locked trade plan remains immutable.
                signal_id = self.history.refresh_duplicate(int(live_trade["id"]), payload)
                self.observations.promote(observation_id, signal_id)
                self.candidates.resolve_market(owner_telegram_id, payload["symbol"], timeframe, promoted_signal_id=signal_id)
                return signal_id
            if owner_telegram_id is None:
                return None
            self.candidates.upsert(
                owner_telegram_id=owner_telegram_id,
                notification_chat_id=notification_chat_id,
                symbol=payload["symbol"],
                timeframe=timeframe,
                side=payload["side"],
                observation_id=observation_id,
                blocked_by_signal_id=int(live_trade["id"]),
                snapshot={"analysis": analysis, "payload": payload},
            )
            return None

        # Pending direction flips replace the old pending plan. Same-side repeats update
        # the existing row instead of creating another Signal ID.
        for stale in opposite:
            self.history.invalidate_open(int(stale["id"]), "DIRECTION_FLIP")
        duplicate = same_side[0] if same_side else None
        for extra in same_side[1:]:
            self.history.invalidate_open(int(extra["id"]), "DUPLICATE_CONSOLIDATED")
        if duplicate:
            signal_id = self.history.refresh_duplicate(int(duplicate["id"]), payload)
        else:
            signal_id = self.history.save(payload)
            if status == "ACTIVE":
                from datetime import datetime, timezone
                now = datetime.now(timezone.utc).isoformat()
                self.history.update_lifecycle(
                    signal_id,
                    activated_at=now,
                    current_price=float(payload["entry"]),
                    effective_stop=float(payload["stop"]),
                    highest_price=float(payload["entry"]),
                    lowest_price=float(payload["entry"]),
                )
        self.observations.promote(observation_id, signal_id)
        if owner_telegram_id is not None:
            self.candidates.resolve_market(owner_telegram_id, payload["symbol"], timeframe, promoted_signal_id=signal_id)
        return signal_id
