from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from database.database import connect


FEATURE_VERSION = "research-features-v1"
RANK_VERSION = "research-rank-v1"
STRATEGY_VERSIONS = {
    "LIQUIDITY_SMC": "liquidity-smc-v1",
    "TREND_FOLLOWING": "trend-following-v1",
    "BREAKOUT": "breakout-v1",
    "MEAN_REVERSION": "mean-reversion-v1",
}
TERMINAL = {"TP3", "STOP", "BREAKEVEN", "MANUAL_STOP", "INVALIDATED", "EXPIRED", "CLOSED"}
MANUAL_REASONS = {"MANUAL", "MANUAL_CLOSE", "MANUAL_STOP", "PANIC", "PANIC_CLOSE"}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False, default=str)


def _checksum(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _loads(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError, OverflowError):
        return default


def _text(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("code") or value.get("label") or value.get("state")
    return " ".join(str(value or "").strip().upper().split())


def _value(features: dict[str, Any], *keys: str) -> Any:
    extras = features.get("extras") if isinstance(features.get("extras"), dict) else {}
    for key in keys:
        if key in features and features[key] is not None and features[key] != "":
            return features[key]
        if key in extras and extras[key] is not None and extras[key] != "":
            return extras[key]
    return None


class ResearchEngine:
    """Immutable decision snapshots plus append-only outcomes and shadow strategies.

    This module deliberately has no imports from order, risk, sizing, or execution services.
    Its outputs are diagnostic records only.
    """

    @staticmethod
    def classify_regimes(features: dict[str, Any], side: str) -> tuple[str, ...]:
        regime = _text(_value(features, "market_regime", "regime"))
        trend = _text(_value(features, "trend", "htf_alignment", "multi_timeframe"))
        volatility = _text(_value(features, "volatility_state", "volatility"))
        structure = " ".join((_text(_value(features, "structure")),
                              _text(_value(features, "bos")),
                              _text(_value(features, "displacement"))))
        atr_pct = _number(_value(features, "atr_pct"))
        result: list[str] = []
        if "TREND" in regime or "TREND" in trend:
            if any(word in f"{regime} {trend}" for word in ("UP", "BULL", "LONG")):
                result.append("TREND_UP")
            if any(word in f"{regime} {trend}" for word in ("DOWN", "BEAR", "SHORT")):
                result.append("TREND_DOWN")
        if any(word in regime for word in ("RANG", "CHOP")) or "RANGE" in trend:
            result.append("RANGE")
        if "COMPRESS" in regime or "COMPRESS" in volatility or "SQUEEZE" in regime:
            result.append("COMPRESSION")
        if any(word in f"{regime} {structure}" for word in ("BREAKOUT", "EXPANSION", "DISPLACEMENT")):
            result.append("BREAKOUT")
        if any(word in f"{regime} {volatility}" for word in ("HIGH", "EXTREME", "VOLATILE")) or atr_pct >= 2.5:
            result.append("HIGH_VOLATILITY")
        if any(word in f"{regime} {volatility}" for word in ("LOW", "COMPRESSED")) or 0 < atr_pct <= .5:
            result.append("LOW_VOLATILITY")
        ordered = tuple(dict.fromkeys(result))
        return ordered or ("UNKNOWN",)

    @staticmethod
    def _primary(regimes: tuple[str, ...]) -> str:
        for key in ("BREAKOUT", "TREND_UP", "TREND_DOWN", "COMPRESSION", "RANGE",
                    "HIGH_VOLATILITY", "LOW_VOLATILITY", "UNKNOWN"):
            if key in regimes:
                return key
        return "UNKNOWN"

    @staticmethod
    def _session(timestamp: str) -> str:
        try:
            value = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
            value = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
            hour = value.astimezone(timezone.utc).hour
        except (TypeError, ValueError):
            return "UNKNOWN"
        if 0 <= hour < 8:
            return "ASIA"
        if 8 <= hour < 13:
            return "LONDON"
        if 13 <= hour < 21:
            return "NEW_YORK"
        return "OFF_HOURS"

    @staticmethod
    def _confidence_bucket(confidence: float) -> str:
        floor = int(max(0, min(99, confidence)) // 10 * 10)
        return f"{floor:02d}-{floor + 9:02d}"

    def capture_signal(self, signal_id: int) -> dict[str, Any] | None:
        with connect() as conn:
            raw = conn.execute("SELECT * FROM signals WHERE id=?", (signal_id,)).fetchone()
        if raw is None:
            return None
        signal = dict(raw)
        features = _loads(signal.get("trade_dna_json") or signal.get("features_json"), {})
        if not isinstance(features, dict):
            features = {}
        decision_at = str(signal.get("created_at") or signal.get("activated_at") or datetime.now(timezone.utc).isoformat())
        regimes = self.classify_regimes(features, str(signal.get("side") or ""))
        capture_quality = ("LATE_TERMINAL_BACKFILL" if str(signal.get("status") or "").upper() in TERMINAL
                           or signal.get("closed_at") else "DECISION_TIME")
        snapshot = {
            "signal_id": int(signal["id"]), "owner_telegram_id": signal.get("owner_telegram_id"),
            "symbol": str(signal.get("symbol") or "").upper(),
            "timeframe": str(signal.get("timeframe") or "").lower(),
            "side": str(signal.get("side") or "").upper(),
            "setup_family": str(signal.get("setup_key") or "UNKNOWN"),
            "decision_at": decision_at, "session": self._session(decision_at),
            "confidence": _number(signal.get("dynamic_confidence") if signal.get("dynamic_confidence") is not None
                                   else signal.get("confidence")),
            "bull_score": _number(signal.get("bull_score")), "bear_score": _number(signal.get("bear_score")),
            "recommendation": signal.get("recommendation"),
            "entry": signal.get("entry"), "stop": signal.get("stop"),
            "take_profits": [signal.get("tp1"), signal.get("tp2"), signal.get("tp3")],
            "rr": signal.get("rr"), "regimes": list(regimes), "primary_regime": self._primary(regimes),
            "features": features, "feature_version": FEATURE_VERSION,
        }
        source_checksum = _checksum(snapshot)
        snapshot_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"research:{signal_id}:{source_checksum}"))
        now = datetime.now(timezone.utc).isoformat()
        with connect() as conn:
            conn.execute("""INSERT INTO research_signal_snapshots(snapshot_id,signal_id,owner_telegram_id,
                symbol,timeframe,side,strategy_key,setup_family,decision_at,captured_at,capture_quality,
                feature_version,source_checksum,primary_regime,regimes_json,confidence_bucket,session_key,
                snapshot_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(signal_id) DO NOTHING""", (
                snapshot_id, signal_id, signal.get("owner_telegram_id"), snapshot["symbol"],
                snapshot["timeframe"], snapshot["side"], "LIQUIDITY_SMC",
                snapshot["setup_family"], decision_at, now, capture_quality, FEATURE_VERSION,
                source_checksum, snapshot["primary_regime"], _canonical(regimes),
                self._confidence_bucket(snapshot["confidence"]), snapshot["session"], _canonical(snapshot)))
            stored = conn.execute("SELECT * FROM research_signal_snapshots WHERE signal_id=?",
                                  (signal_id,)).fetchone()
        if stored:
            item = dict(stored)
            self.evaluate_strategies(item)
            self.rank_snapshot(item)
            return item
        return None

    def capture_pending(self, limit: int = 200) -> int:
        safe = max(1, min(int(limit), 1000))
        with connect() as conn:
            rows = conn.execute(f"""SELECT s.id FROM signals s WHERE NOT EXISTS(
                SELECT 1 FROM research_signal_snapshots r WHERE r.signal_id=s.id)
                ORDER BY s.id DESC LIMIT {safe}""").fetchall()
        captured = 0
        for row in rows:
            try:
                captured += int(self.capture_signal(int(row["id"])) is not None)
            except Exception:
                logging.exception("research_capture_failed signal_id=%s", row["id"])
        return captured

    @staticmethod
    def _strategy_decision(strategy: str, snapshot: dict[str, Any]) -> dict[str, Any]:
        features = snapshot.get("features") if isinstance(snapshot.get("features"), dict) else {}
        side = str(snapshot.get("side") or "NEUTRAL").upper()
        regimes = set(snapshot.get("regimes") or [])
        confidence = _number(snapshot.get("confidence"))
        rsi = _number(_value(features, "rsi"), 50)
        bos = _text(_value(features, "bos", "structure"))
        evidence: list[str] = []
        action = "WAIT"
        if strategy == "LIQUIDITY_SMC":
            action, evidence = "ACCEPT", ["production signal passed deterministic signal creation"]
        elif strategy == "TREND_FOLLOWING":
            aligned = (side == "LONG" and "TREND_UP" in regimes) or (side == "SHORT" and "TREND_DOWN" in regimes)
            opposite = (side == "LONG" and "TREND_DOWN" in regimes) or (side == "SHORT" and "TREND_UP" in regimes)
            action = "ACCEPT" if aligned else "REJECT" if opposite else "WAIT"
            evidence = ["deterministic regime aligned" if aligned else "trend regime unavailable or opposed"]
        elif strategy == "BREAKOUT":
            confirmed = "BREAKOUT" in regimes or any(word in bos for word in ("BOS", "BREAK", "DISPLACEMENT"))
            action = "ACCEPT" if confirmed else "WAIT"
            evidence = ["breakout or structure confirmation present" if confirmed else "no confirmed breakout evidence"]
        elif strategy == "MEAN_REVERSION":
            aligned = "RANGE" in regimes and ((side == "LONG" and rsi <= 35) or (side == "SHORT" and rsi >= 65))
            action = "ACCEPT" if aligned else "WAIT" if "RANGE" in regimes else "REJECT"
            evidence = [f"range={('RANGE' in regimes)} rsi={rsi:.1f}"]
        return {"action": action, "direction": side,
                "confidence": max(0.0, min(100.0, confidence if action == "ACCEPT" else confidence * .6)),
                "evidence": evidence}

    def evaluate_strategies(self, snapshot_row: dict[str, Any]) -> int:
        snapshot = _loads(snapshot_row.get("snapshot_json"), {})
        created = 0
        now = datetime.now(timezone.utc).isoformat()
        for strategy, version in STRATEGY_VERSIONS.items():
            decision = self._strategy_decision(strategy, snapshot)
            payload = {"strategy": strategy, "version": version, **decision,
                       "entry": snapshot.get("entry"), "stop": snapshot.get("stop"),
                       "targets": snapshot.get("take_profits") or []}
            with connect() as conn:
                cur = conn.execute("""INSERT INTO research_strategy_decisions(snapshot_id,signal_id,
                    strategy_key,strategy_version,action,direction,confidence,hypothetical_entry,
                    hypothetical_stop,hypothetical_targets_json,evidence_json,decision_checksum,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(snapshot_id,strategy_key,strategy_version)
                    DO NOTHING""", (snapshot_row["snapshot_id"], snapshot_row["signal_id"], strategy,
                    version, decision["action"], decision["direction"], decision["confidence"],
                    snapshot.get("entry"), snapshot.get("stop"), _canonical(snapshot.get("take_profits") or []),
                    _canonical(decision["evidence"]), _checksum(payload), now))
            created += int(cur.rowcount == 1)
        return created

    def rank_snapshot(self, snapshot_row: dict[str, Any]) -> dict[str, Any]:
        snapshot = _loads(snapshot_row.get("snapshot_json"), {})
        features = snapshot.get("features") if isinstance(snapshot.get("features"), dict) else {}
        regimes = set(snapshot.get("regimes") or [])
        side = str(snapshot.get("side") or "")
        aligned = (side == "LONG" and "TREND_UP" in regimes) or (side == "SHORT" and "TREND_DOWN" in regimes)
        evidence_flags = sum(_text(_value(features, key)) not in {"", "UNKNOWN", "NONE"}
                             for key in ("bos", "choch", "sweep", "fvg", "order_block"))
        components = {
            "confidence": min(30.0, _number(snapshot.get("confidence")) * .3),
            "reward_risk": min(20.0, max(0.0, _number(snapshot.get("rr"))) / 3 * 20),
            "regime_alignment": 20.0 if aligned else 10.0 if "UNKNOWN" not in regimes else 0.0,
            "feature_evidence": min(20.0, evidence_flags * 4.0),
            "data_quality": 10.0 if snapshot_row.get("capture_quality") == "DECISION_TIME" else 3.0,
        }
        score = round(sum(components.values()), 3)
        now = datetime.now(timezone.utc).isoformat()
        with connect() as conn:
            conn.execute("""INSERT INTO research_signal_rankings(snapshot_id,signal_id,rank_version,
                diagnostic_score,components_json,created_at) VALUES(?,?,?,?,?,?)
                ON CONFLICT(snapshot_id,rank_version) DO NOTHING""", (
                snapshot_row["snapshot_id"], snapshot_row["signal_id"], RANK_VERSION,
                score, _canonical(components), now))
            row = conn.execute("SELECT * FROM research_signal_rankings WHERE snapshot_id=? AND rank_version=?",
                               (snapshot_row["snapshot_id"], RANK_VERSION)).fetchone()
        return dict(row)

    def attach_outcome(self, snapshot_row: dict[str, Any]) -> bool:
        signal_id = int(snapshot_row["signal_id"])
        with connect() as conn:
            signal_row = conn.execute("SELECT * FROM signals WHERE id=?", (signal_id,)).fetchone()
            if signal_row is None:
                return False
            signal = dict(signal_row)
            status = str(signal.get("status") or "").upper()
            if status not in TERMINAL and not signal.get("closed_at"):
                return False
            event_rows = [dict(row) for row in conn.execute(
                "SELECT event_type,created_at FROM signal_events WHERE signal_id=? ORDER BY id", (signal_id,)).fetchall()]
            policy_rows = [dict(row) for row in conn.execute("""SELECT telegram_id,status,code,reason
                FROM copy_execution_journal WHERE signal_id=? ORDER BY id""", (signal_id,)).fetchall()]
            execution_rows = [dict(row) for row in conn.execute("""SELECT * FROM paper_execution_positions
                WHERE signal_id=? ORDER BY telegram_id,id""", (signal_id,)).fetchall()]
            fill_rows = [dict(row) for row in conn.execute("""SELECT telegram_id,
                COALESCE(SUM(commission),0) fees,COALESCE(AVG(slippage_pct),0) slippage
                FROM paper_execution_fills WHERE signal_id=? GROUP BY telegram_id""", (signal_id,)).fetchall()]
        fill_by_user = {int(row["telegram_id"]): row for row in fill_rows}
        executions = []
        manual = False
        for row in execution_rows:
            reason = str(row.get("close_reason") or "").upper()
            intervention = any(token in reason for token in MANUAL_REASONS)
            manual = manual or intervention
            fills = fill_by_user.get(int(row["telegram_id"]), {})
            executions.append({
                "telegram_id": row["telegram_id"], "status": row["status"],
                "realized_r": row.get("realized_r"), "realized_pnl": row.get("realized_pnl"),
                "fees": row.get("total_commission") or fills.get("fees") or 0,
                "average_slippage_pct": fills.get("slippage") or 0,
                "manual_intervention": intervention, "close_reason": row.get("close_reason"),
            })
        result = str(signal.get("result") or status)
        signal_manual = any(token in result.upper() for token in MANUAL_REASONS)
        signal_r = None if signal.get("realized_r") is None else _number(signal.get("realized_r"))
        no_intervention = signal_r if manual and not signal_manual else None
        for policy in policy_rows:
            accepted = str(policy.get("status") or "").upper() in {
                "SENT", "EXECUTED", "SUCCEEDED", "COMPLETED"
            }
            policy["policy_r"] = signal_r if accepted and not signal_manual else None
            policy["outcome_basis"] = "SIGNAL_POLICY_COUNTERFACTUAL" if accepted else "NOT_EXECUTED"
        progression = {key: any(str(row["event_type"]).upper() == key for row in event_rows)
                       or status == key or result.upper() == key for key in ("TP1", "TP2", "TP3")}
        outcome = {
            "signal_id": signal_id, "signal_result": result, "signal_r": signal_r,
            "mfe_pct": _number(signal.get("max_profit_pct")),
            "mae_pct": _number(signal.get("max_drawdown_pct")),
            "tp_progression": progression, "stop_reached": result.upper() == "STOP" or status == "STOP",
            "activated_at": signal.get("activated_at"), "closed_at": signal.get("closed_at"),
            "policy_outcomes": policy_rows, "execution_outcomes": executions,
            "manual_intervention": manual or signal_manual, "no_intervention_r": no_intervention,
        }
        outcome_checksum = _checksum(outcome)
        now = datetime.now(timezone.utc).isoformat()
        with connect() as conn:
            previous = conn.execute("SELECT COALESCE(MAX(outcome_version),0) version FROM research_outcomes WHERE snapshot_id=?",
                                    (snapshot_row["snapshot_id"],)).fetchone()
            cur = conn.execute("""INSERT INTO research_outcomes(snapshot_id,signal_id,outcome_checksum,
                outcome_version,signal_result,signal_r,mfe_pct,mae_pct,tp_progression_json,stop_reached,
                policy_outcomes_json,execution_outcomes_json,manual_intervention,no_intervention_r,
                outcome_json,resolved_at,attached_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(snapshot_id,outcome_checksum) DO NOTHING""", (
                snapshot_row["snapshot_id"], signal_id, outcome_checksum, int(previous["version"] or 0) + 1,
                result, signal_r, outcome["mfe_pct"], outcome["mae_pct"], _canonical(progression),
                int(outcome["stop_reached"]), _canonical(policy_rows), _canonical(executions),
                int(outcome["manual_intervention"]), no_intervention, _canonical(outcome),
                str(signal.get("closed_at") or signal.get("updated_at") or now), now))
        return cur.rowcount == 1

    def attach_resolved(self, limit: int = 500) -> int:
        safe = max(1, min(int(limit), 2000))
        with connect() as conn:
            rows = [dict(row) for row in conn.execute(f"""SELECT r.* FROM research_signal_snapshots r
                JOIN signals s ON s.id=r.signal_id WHERE s.closed_at IS NOT NULL OR
                UPPER(s.status) IN ('TP3','STOP','BREAKEVEN','MANUAL_STOP','INVALIDATED','EXPIRED','CLOSED')
                ORDER BY r.id DESC LIMIT {safe}""").fetchall()]
        return sum(self.attach_outcome(row) for row in rows)

    def run_cycle(self, limit: int = 200) -> dict[str, int]:
        captured = self.capture_pending(limit)
        outcomes = self.attach_resolved(limit * 2)
        return {"captured": captured, "outcomes_attached": outcomes}

    @staticmethod
    def _rows(telegram_id: int | None = None) -> list[dict[str, Any]]:
        with connect() as conn:
            rows = [dict(row) for row in conn.execute("""SELECT r.*,o.outcome_json,k.diagnostic_score,
                k.components_json FROM research_signal_snapshots r
                LEFT JOIN research_outcomes o ON o.snapshot_id=r.snapshot_id AND o.id=(
                    SELECT MAX(o2.id) FROM research_outcomes o2 WHERE o2.snapshot_id=r.snapshot_id)
                LEFT JOIN research_signal_rankings k ON k.snapshot_id=r.snapshot_id AND k.rank_version=?
                WHERE (? IS NULL OR r.owner_telegram_id IS NULL OR r.owner_telegram_id=0
                    OR r.owner_telegram_id=?) ORDER BY r.id""",
                (RANK_VERSION, telegram_id, telegram_id)).fetchall()]
        for row in rows:
            row["snapshot"] = _loads(row.get("snapshot_json"), {})
            row["outcome"] = _loads(row.get("outcome_json"), {})
        return rows

    @staticmethod
    def metrics(rows: list[dict[str, Any]], minimum_samples: int = 20) -> dict[str, Any]:
        resolved = [row for row in rows if row.get("capture_quality") == "DECISION_TIME"
                    and row.get("outcome") and row["outcome"].get("signal_r") is not None
                    and not row["outcome"].get("manual_intervention")]
        rs = [_number(row["outcome"]["signal_r"]) for row in resolved]
        wins, losses = [r for r in rs if r > 0], [r for r in rs if r < 0]
        equity = peak = drawdown = 0.0
        for value in rs:
            equity += value
            peak = max(peak, equity)
            drawdown = min(drawdown, equity - peak)
        count = len(resolved)
        return {
            "sample_size": count, "win_rate": len(wins) / count if count else None,
            "expectancy_r": sum(rs) / count if count else None,
            "average_win_r": sum(wins) / len(wins) if wins else None,
            "average_loss_r": sum(losses) / len(losses) if losses else None,
            "profit_factor": sum(wins) / abs(sum(losses)) if losses else None,
            "average_mfe_pct": sum(_number(row["outcome"].get("mfe_pct")) for row in resolved) / count if count else None,
            "average_mae_pct": sum(_number(row["outcome"].get("mae_pct")) for row in resolved) / count if count else None,
            "drawdown_proxy_r": drawdown if count else None,
            "manual_excluded": sum(bool(row.get("outcome", {}).get("manual_intervention")) for row in rows),
            "late_backfill_excluded": sum(row.get("capture_quality") != "DECISION_TIME" for row in rows),
            "status": "SUFFICIENT" if count >= minimum_samples else "INSUFFICIENT_SAMPLES",
            "minimum_samples": minimum_samples,
        }

    def cohort_report(self, telegram_id: int | None = None, minimum_samples: int | None = None) -> dict[str, Any]:
        minimum = minimum_samples or max(3, int(os.getenv("RESEARCH_MIN_SAMPLES", "20")))
        rows = self._rows(telegram_id)
        dimensions: dict[str, dict[str, list[dict[str, Any]]]] = {
            key: defaultdict(list) for key in ("strategy", "timeframe", "direction", "regime",
                                               "confidence", "symbol", "session", "feature_combo")}
        for row in rows:
            snap = row["snapshot"]
            features = snap.get("features") if isinstance(snap.get("features"), dict) else {}
            combo = "+".join(key.upper() for key in ("bos", "choch", "sweep", "fvg", "order_block")
                             if _text(_value(features, key)) not in {"", "UNKNOWN", "NONE"}) or "NO_MAJOR_FEATURE"
            mapping = {
                "strategy": row.get("strategy_key"), "timeframe": row.get("timeframe"),
                "direction": row.get("side"), "regime": row.get("primary_regime"),
                "confidence": row.get("confidence_bucket"), "symbol": row.get("symbol"),
                "session": row.get("session_key"), "feature_combo": combo,
            }
            for dimension, value in mapping.items():
                dimensions[dimension][str(value or "UNKNOWN")].append(row)
        reports = {dimension: [{"cohort": cohort, **self.metrics(items, minimum)}
                               for cohort, items in sorted(groups.items())]
                   for dimension, groups in dimensions.items()}
        resolved = sum(bool(row.get("outcome")) for row in rows)
        return {"snapshots": len(rows), "resolved": resolved, "overall": self.metrics(rows, minimum),
                "dimensions": reports, "minimum_samples": minimum,
                "warning": "Descriptive associations are not causal evidence."}

    def strategy_comparison(self, telegram_id: int | None = None,
                            minimum_samples: int | None = None) -> dict[str, Any]:
        minimum = minimum_samples or max(3, int(os.getenv("RESEARCH_MIN_SAMPLES", "20")))
        with connect() as conn:
            rows = [dict(row) for row in conn.execute("""SELECT d.*,r.owner_telegram_id,o.outcome_json
                FROM research_strategy_decisions d JOIN research_signal_snapshots r ON r.snapshot_id=d.snapshot_id
                LEFT JOIN research_outcomes o ON o.snapshot_id=d.snapshot_id AND o.id=(
                    SELECT MAX(o2.id) FROM research_outcomes o2 WHERE o2.snapshot_id=d.snapshot_id)
                WHERE r.capture_quality='DECISION_TIME' AND
                    (? IS NULL OR r.owner_telegram_id IS NULL OR r.owner_telegram_id=0
                     OR r.owner_telegram_id=?) ORDER BY d.id""",
                (telegram_id, telegram_id)).fetchall()]
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            outcome = _loads(row.get("outcome_json"), {})
            if not outcome or outcome.get("signal_r") is None or outcome.get("manual_intervention"):
                continue
            row["outcome"] = outcome
            grouped[row["strategy_key"]].append(row)
        result = []
        for strategy, items in sorted(grouped.items()):
            accepted = [row for row in items if row["action"] == "ACCEPT"]
            rs = [_number(row["outcome"]["signal_r"]) for row in accepted]
            missed_wins = sum(row["action"] != "ACCEPT" and _number(row["outcome"]["signal_r"]) > 0 for row in items)
            result.append({"strategy": strategy, "identical_resolved_snapshots": len(items),
                           "accepted": len(accepted), "coverage": len(accepted) / len(items) if items else 0,
                           "win_rate": sum(value > 0 for value in rs) / len(rs) if rs else None,
                           "expectancy_r": sum(rs) / len(rs) if rs else None,
                           "missed_wins": missed_wins,
                           "status": "SUFFICIENT" if len(accepted) >= minimum else "INSUFFICIENT_SAMPLES"})
        return {"strategies": result, "minimum_samples": minimum,
                "execution_authority": False, "comparison_basis": "IDENTICAL_SIGNAL_SNAPSHOTS"}

    def edge_report(self, telegram_id: int | None = None) -> dict[str, Any]:
        report = self.cohort_report(telegram_id)
        cohorts = [dict(item, dimension=dimension) for dimension, values in report["dimensions"].items()
                   for item in values if item["status"] == "SUFFICIENT" and item["expectancy_r"] is not None]
        strongest = sorted(cohorts, key=lambda item: (-item["expectancy_r"], -item["sample_size"]))[:10]
        weakest = sorted(cohorts, key=lambda item: (item["expectancy_r"], -item["sample_size"]))[:10]
        return {**report, "strongest_descriptive_cohorts": strongest,
                "weakest_descriptive_cohorts": weakest,
                "claim": "NO_CAUSAL_OR_PROFITABILITY_CLAIM"}

    def rankings(self, telegram_id: int | None = None, limit: int = 10) -> list[dict[str, Any]]:
        with connect() as conn:
            rows = conn.execute("""SELECT k.*,r.symbol,r.timeframe,r.side,r.primary_regime
                FROM research_signal_rankings k JOIN research_signal_snapshots r ON r.snapshot_id=k.snapshot_id
                WHERE k.rank_version=? AND r.capture_quality='DECISION_TIME'
                    AND (? IS NULL OR r.owner_telegram_id IS NULL OR r.owner_telegram_id=0
                         OR r.owner_telegram_id=?)
                ORDER BY k.diagnostic_score DESC,k.id DESC LIMIT ?""",
                (RANK_VERSION, telegram_id, telegram_id, max(1, min(int(limit), 50)))).fetchall()
        return [dict(row) for row in rows]

    def scalping_report(self, telegram_id: int | None = None) -> dict[str, Any]:
        rows = [row for row in self._rows(telegram_id) if row.get("capture_quality") == "DECISION_TIME"
                and row["timeframe"] in {"1m", "3m", "5m"}
                and row.get("outcome") and row["outcome"].get("signal_r") is not None
                and not row["outcome"].get("manual_intervention")]
        fee = max(0.0, _number(os.getenv("SCALPING_TAKER_FEE_PCT", "0.05")))
        spread = max(0.0, _number(os.getenv("SCALPING_SPREAD_PCT", "0.02")))
        slippage = max(0.0, _number(os.getenv("SCALPING_SLIPPAGE_PCT", "0.03")))
        latency = max(0.0, _number(os.getenv("SCALPING_LATENCY_PENALTY_PCT", "0.01")))
        roundtrip_cost_pct = 2 * fee + spread + 2 * slippage + latency
        groups: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            snapshot = row["snapshot"]
            entry, stop = _number(snapshot.get("entry")), _number(snapshot.get("stop"))
            risk_pct = abs(entry - stop) / entry * 100 if entry else 0
            cost_r = roundtrip_cost_pct / risk_pct if risk_pct > 0 else float("inf")
            if math.isfinite(cost_r):
                groups[row["timeframe"]].append(_number(row["outcome"]["signal_r"]) - cost_r)
        minimum = max(10, int(os.getenv("SCALPING_MIN_SAMPLES", "100")))
        result = {key: {"samples": len(values),
                        "after_cost_expectancy_r": sum(values) / len(values) if values else None,
                        "positive_after_cost": bool(values and len(values) >= minimum and sum(values) / len(values) > 0),
                        "status": "SUFFICIENT" if len(values) >= minimum else "INSUFFICIENT_SAMPLES"}
                  for key, values in sorted(groups.items())}
        return {"timeframes": result, "roundtrip_cost_pct": roundtrip_cost_pct,
                "minimum_required_movement_pct": roundtrip_cost_pct,
                "assumptions": {"taker_fee_each_side_pct": fee, "spread_pct": spread,
                                "slippage_each_side_pct": slippage, "latency_penalty_pct": latency},
                "minimum_samples": minimum, "mode": "PAPER_SHADOW_ONLY"}
