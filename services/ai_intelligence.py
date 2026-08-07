from __future__ import annotations

import json
import math
import os
import re
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from database.database import connect, database_backend


ACCEPT_ACTIONS = {"ACCEPT_REDUCED", "ACCEPT_STANDARD"}
POSITIVE_RESULTS = {"WIN", "TP1", "TP2", "TP3", "MANUAL_PROFIT"}
REGIMES = ("TREND", "RANGE", "BREAKOUT", "COMPRESSION", "VOLATILITY",
           "LIQUIDATION_EVENT", "NEWS_ANOMALY", "MOMENTUM_EXHAUSTION")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False)


def _loads(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(value or "")
        return parsed
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError, OverflowError):
        return default


def _normalized_factor(value: Any) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", str(value).lower()).split())[:160]


def _feature_map(value: Any, prefix: str = "", depth: int = 0) -> dict[str, Any]:
    if depth > 3:
        return {}
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key in sorted(value)[:80]:
            child = f"{prefix}.{key}" if prefix else str(key)
            result.update(_feature_map(value[key], child, depth + 1))
        return result
    if isinstance(value, (str, bool, int, float)) and value is not None:
        return {prefix: value}
    return {}


class AIObservationIntelligence:
    """Derived advisory intelligence. This service has no execution dependencies or authority."""

    @staticmethod
    def material_state_checksum(context: Any) -> str:
        from services.ai_trading import checksum

        market = context.market
        price = max(abs(_safe_float(market.get("price"))), 1e-12)
        normalized_market = {
            "entry_ratio": round(_safe_float(market.get("entry")) / price, 5),
            "stop_ratio": round(_safe_float(market.get("stop")) / price, 5),
            "tp_ratios": [round(_safe_float(value) / price, 5)
                          for value in market.get("take_profits", [])],
            "rr": round(_safe_float(market.get("expected_rr")), 3),
            "price_bucket": round(math.log10(price), 4),
        }
        history = context.history if isinstance(context.history, dict) else {}
        learning = history.get("learned_patterns") if isinstance(history.get("learned_patterns"), dict) else {}
        return checksum({
            "symbol": context.symbol, "timeframe": context.timeframe,
            "market": normalized_market, "features": context.features,
            "deterministic": context.deterministic,
            "portfolio": context.portfolio,
            "closed_history": history.get("similar_trades", []),
            "prior_ai_decisions": history.get("prior_ai_decisions", []),
            "learning_snapshot": learning.get("snapshot_key"),
        })

    @staticmethod
    def reusable_decision(identity_checksum: str, material_checksum: str) -> dict[str, Any] | None:
        if os.getenv("AI_OBSERVATION_CACHE_ENABLED", "true").strip().lower() not in {"1", "true", "yes", "on"}:
            return None
        try:
            ttl = max(0, min(3600, int(os.getenv("AI_OBSERVATION_CACHE_TTL_SECONDS", "180"))))
        except ValueError:
            ttl = 180
        if ttl <= 0:
            return None
        since = (datetime.now(timezone.utc) - timedelta(seconds=ttl)).isoformat()
        with connect() as conn:
            row = conn.execute("""SELECT * FROM ai_decisions
                WHERE provider_identity_checksum=? AND material_state_checksum=?
                  AND validation_code='VALID' AND created_at>=?
                ORDER BY id DESC LIMIT 1""", (identity_checksum, material_checksum, since)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def decision_from_row(row: dict[str, Any]):
        from services.ai_trading import AIAction, ValidatedDecision

        regimes = tuple(_loads(row.get("regime_tags_json"), [row.get("regime") or "UNKNOWN"]))
        return ValidatedDecision(
            str(row.get("regime") or "UNKNOWN"), str(row.get("direction") or "NEUTRAL"),
            _safe_float(row.get("raw_confidence")), _safe_float(row.get("uncertainty"), 100),
            AIAction(str(row.get("recommended_action") or "ABSTAIN")),
            _safe_float(row.get("recommended_risk_multiplier")), bool(row.get("abstention")),
            tuple(_loads(row.get("supporting_factors_json"), [])),
            tuple(_loads(row.get("conflicting_factors_json"), [])),
            tuple(_loads(row.get("invalidation_conditions_json"), [])),
            str(row.get("explanation") or "Reused unchanged advisory observation."),
            regimes, _safe_float(row.get("opportunity_quality")),
            tuple(_loads(row.get("evidence_ranking_json"), [])),
            str(row.get("uncertainty_explanation") or "Unchanged material market state."),
            True, "VALID", "COMPLETE",
        )

    @staticmethod
    def _regimes(row: dict[str, Any], signal: dict[str, Any]) -> list[str]:
        values = [str(value).upper() for value in _loads(row.get("regime_tags_json"), [])]
        text = " ".join((str(row.get("regime") or ""), str(signal.get("setup_key") or ""),
                         str(signal.get("features_json") or ""))).lower()
        keywords = {
            "TREND": ("trend", "directional"), "RANGE": ("range", "ranging", "chop"),
            "BREAKOUT": ("breakout", "break out", "displacement"),
            "COMPRESSION": ("compression", "compressed", "squeeze"),
            "VOLATILITY": ("volatil", "expansion", "atr"),
            "LIQUIDATION_EVENT": ("liquidation", "cascade"),
            "NEWS_ANOMALY": ("news", "headline", "event risk"),
            "MOMENTUM_EXHAUSTION": ("exhaust", "divergence", "overbought", "oversold"),
        }
        for regime, terms in keywords.items():
            if any(term in text for term in terms):
                values.append(regime)
        result = [value for value in dict.fromkeys(values) if value in REGIMES]
        return result or ["UNKNOWN"]

    @staticmethod
    def _similarity(left: dict[str, Any], right: dict[str, Any]) -> tuple[float, list[str], list[str]]:
        score = total = 0.0
        matches: list[str] = []
        differences: list[str] = []
        for field, weight in (("symbol", .5), ("timeframe", 1.0), ("side", 1.5), ("setup_key", 2.0)):
            a, b = str(left.get(field) or "UNKNOWN").lower(), str(right.get(field) or "UNKNOWN").lower()
            total += weight
            if a == b:
                score += weight
                matches.append(f"same {field.replace('_', ' ')}")
            else:
                differences.append(f"different {field.replace('_', ' ')}")
        a_features = _feature_map(_loads(left.get("features_json"), {}))
        b_features = _feature_map(_loads(right.get("features_json"), {}))
        for key in sorted(set(a_features) & set(b_features))[:60]:
            a, b = a_features[key], b_features[key]
            total += 1
            if isinstance(a, (int, float)) and not isinstance(a, bool) and isinstance(b, (int, float)) and not isinstance(b, bool):
                scale = max(abs(_safe_float(a)), abs(_safe_float(b)), 1.0)
                closeness = max(0.0, 1 - abs(_safe_float(a) - _safe_float(b)) / scale)
            else:
                closeness = 1.0 if str(a).lower() == str(b).lower() else 0.0
            score += closeness
            if closeness >= .9 and len(matches) < 8:
                matches.append(f"similar {key}")
            elif closeness <= .2 and len(differences) < 8:
                differences.append(f"different {key}")
        return round(score / total * 100 if total else 0.0, 2), matches[:6], differences[:6]

    def enrich_decision(self, decision_id: str) -> dict[str, Any] | None:
        with connect() as conn:
            row = conn.execute("""SELECT d.*,s.side,s.setup_key,s.features_json,s.status signal_status,
                    s.result,s.realized_r signal_realized_r,r.snapshot_json,r.capture_quality
                    FROM ai_decisions d JOIN signals s ON s.id=d.signal_id
                    LEFT JOIN research_signal_snapshots r ON r.signal_id=d.signal_id
                    WHERE d.decision_id=?""", (decision_id,)).fetchone()
            if not row:
                return None
            current = dict(row)
            snapshot = _loads(current.get("snapshot_json"), {})
            if current.get("capture_quality") == "DECISION_TIME" and isinstance(snapshot, dict):
                current["side"] = snapshot.get("side") or current.get("side")
                current["setup_key"] = snapshot.get("setup_family") or current.get("setup_key")
                current["features_json"] = _json(snapshot.get("features") or {})
            candidates = [dict(value) for value in conn.execute("""SELECT d.decision_id,d.signal_id,
                    d.regime,d.regime_tags_json,d.recommended_action,d.opportunity_quality,
                    s.symbol,s.timeframe,s.realized_r,r.snapshot_json
                    FROM ai_decisions d JOIN signals s ON s.id=d.signal_id
                    JOIN research_signal_snapshots r ON r.signal_id=d.signal_id
                        AND r.capture_quality='DECISION_TIME'
                    WHERE d.decision_id<>? AND d.validation_code='VALID'
                      AND (d.telegram_id=? OR (? IS NULL AND d.telegram_id IS NULL))
                    ORDER BY d.id DESC LIMIT 500""", (decision_id, current.get("telegram_id"),
                    current.get("telegram_id"))).fetchall()]

        for candidate in candidates:
            candidate_snapshot = _loads(candidate.pop("snapshot_json", None), {})
            candidate["side"] = candidate_snapshot.get("side")
            candidate["setup_key"] = candidate_snapshot.get("setup_family")
            candidate["features_json"] = _json(candidate_snapshot.get("features") or {})

        similarities = []
        for candidate in candidates:
            score, matching, differences = self._similarity(current, candidate)
            if score < 35:
                continue
            similarities.append({"decision_id": candidate["decision_id"],
                                 "signal_id": int(candidate["signal_id"]), "score": score,
                                 "matching": matching, "differences": differences,
                                 "realized_r": candidate.get("realized_r")})
        similarities.sort(key=lambda value: (-value["score"], -value["signal_id"]))
        similarities = similarities[:8]
        now = _now()
        with connect() as conn:
            for item in similarities:
                conn.execute("""INSERT INTO ai_decision_similarities(source_decision_id,similar_decision_id,
                    similar_signal_id,similarity_score,matching_json,differences_json,outcome_r,created_at)
                    VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(source_decision_id,similar_decision_id) DO UPDATE SET
                    similarity_score=excluded.similarity_score,matching_json=excluded.matching_json,
                    differences_json=excluded.differences_json,outcome_r=excluded.outcome_r""", (
                    decision_id, item["decision_id"], item["signal_id"], item["score"],
                    _json(item["matching"]), _json(item["differences"]), item["realized_r"], now))

        regimes = self._regimes(current, current)
        supporting = _loads(current.get("supporting_factors_json"), [])
        ranking = _loads(current.get("evidence_ranking_json"), []) or supporting
        deterministic = {
            "accepted": None if current.get("deterministic_accepted") is None else bool(current["deterministic_accepted"]),
            "action": current.get("deterministic_action"), "direction": current.get("side"),
            "status": current.get("signal_status"),
        }
        gpt = {"action": current.get("recommended_action"), "direction": current.get("direction"),
               "confidence": _safe_float(current.get("raw_confidence")),
               "uncertainty": _safe_float(current.get("uncertainty")),
               "abstention": bool(current.get("abstention"))}
        from services.ai_trading import checksum
        cluster_key = checksum({"regimes": regimes, "setup": current.get("setup_key"),
                                "timeframe": current.get("timeframe"), "side": current.get("side")})[:20]
        values = (
            decision_id, current["signal_id"], current.get("provider_identity_checksum"),
            _json(deterministic), _json(gpt), _json(regimes),
            _safe_float(current.get("opportunity_quality")), _json(ranking),
            current.get("conflicting_factors_json") or "[]",
            str(current.get("uncertainty_explanation") or "No uncertainty explanation was available."),
            _json(similarities), cluster_key, now, now,
        )
        with connect() as conn:
            conn.execute("""INSERT INTO ai_decision_intelligence(decision_id,signal_id,identity_checksum,
                deterministic_decision_json,gpt_counterfactual_json,market_regimes_json,
                opportunity_quality,evidence_ranking_json,contradictions_json,uncertainty_explanation,
                similarity_summary_json,cluster_key,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(decision_id) DO UPDATE SET
                deterministic_decision_json=excluded.deterministic_decision_json,
                gpt_counterfactual_json=excluded.gpt_counterfactual_json,
                market_regimes_json=excluded.market_regimes_json,
                opportunity_quality=excluded.opportunity_quality,
                evidence_ranking_json=excluded.evidence_ranking_json,
                contradictions_json=excluded.contradictions_json,
                uncertainty_explanation=excluded.uncertainty_explanation,
                similarity_summary_json=excluded.similarity_summary_json,
                cluster_key=excluded.cluster_key,updated_at=excluded.updated_at""", values)
            stored = conn.execute("SELECT * FROM ai_decision_intelligence WHERE decision_id=?",
                                  (decision_id,)).fetchone()
        return dict(stored) if stored else None

    def evaluate_closed(self, limit: int = 200) -> int:
        with connect() as conn:
            rows = [dict(row) for row in conn.execute("""SELECT d.*,s.result,s.realized_r signal_realized_r,
                    s.closed_at,i.market_regimes_json,i.opportunity_quality intelligence_quality,
                    o.intervention_type
                    FROM ai_decisions d JOIN signals s ON s.id=d.signal_id
                    LEFT JOIN ai_decision_intelligence i ON i.decision_id=d.decision_id
                    LEFT JOIN ai_decision_outcomes o ON o.decision_id=d.decision_id
                    LEFT JOIN ai_counterfactual_evaluations e ON e.decision_id=d.decision_id
                    WHERE s.closed_at IS NOT NULL AND e.decision_id IS NULL
                      AND d.validation_code='VALID'
                    ORDER BY d.id LIMIT ?""", (max(1, min(int(limit), 1000)),)).fetchall()]
        created = 0
        for row in rows:
            realized = _safe_float(row.get("signal_realized_r"))
            result = str(row.get("result") or "").upper()
            intervention = row.get("intervention_type")
            if not intervention and any(token in result for token in ("MANUAL", "PANIC", "EARLY_CLOSE")):
                intervention = result
            eligible = not bool(intervention)
            actual = realized > 0 or result in POSITIVE_RESULTS
            gpt = str(row.get("recommended_action")) in ACCEPT_ACTIONS
            deterministic = bool(row.get("deterministic_accepted"))
            classification = "TP" if gpt and actual else "FP" if gpt else "FN" if actual else "TN"
            disagreement = gpt != deterministic
            regimes = _loads(row.get("market_regimes_json"), []) or _loads(row.get("regime_tags_json"), [])
            primary = str(regimes[0] if regimes else row.get("regime") or "UNKNOWN")
            values = (row["decision_id"], row["signal_id"], row.get("provider_identity_checksum"), primary,
                      int(deterministic), int(gpt), int(actual), classification,
                      int(deterministic == actual), int(gpt == actual), int(disagreement),
                      int(disagreement and gpt == actual),
                      _safe_float(row.get("intelligence_quality") or row.get("opportunity_quality")),
                      realized, intervention, int(eligible), _now())
            with connect() as conn:
                cur = conn.execute("""INSERT INTO ai_counterfactual_evaluations(decision_id,signal_id,
                    identity_checksum,primary_regime,deterministic_positive,gpt_positive,actual_positive,
                    classification,deterministic_correct,gpt_correct,disagreement,profitable_disagreement,
                    opportunity_quality,realized_r,intervention_type,evaluation_eligible,evaluated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(decision_id) DO NOTHING""", values)
            created += int(cur.rowcount == 1)
        return created

    @staticmethod
    def _scope(identity_checksum: str | None, telegram_id: int | None) -> tuple[str, list[Any]]:
        clauses, params = ["e.evaluation_eligible=1"], []
        if identity_checksum:
            clauses.append("e.identity_checksum=?")
            params.append(identity_checksum)
        if telegram_id is not None:
            clauses.append("d.telegram_id=?")
            params.append(telegram_id)
        return ("WHERE " + " AND ".join(clauses) if clauses else "", params)

    def counterfactual_report(self, identity_checksum: str | None = None,
                              telegram_id: int | None = None) -> dict[str, Any]:
        where, params = self._scope(identity_checksum, telegram_id)
        with connect() as conn:
            rows = [dict(row) for row in conn.execute(f"""SELECT e.*,d.recommended_action
                FROM ai_counterfactual_evaluations e JOIN ai_decisions d ON d.decision_id=e.decision_id
                {where} ORDER BY e.id""", tuple(params)).fetchall()]
        counts = {label: sum(row["classification"] == label for row in rows) for label in ("TP", "FP", "FN", "TN")}
        precision = counts["TP"] / (counts["TP"] + counts["FP"]) if counts["TP"] + counts["FP"] else None
        recall = counts["TP"] / (counts["TP"] + counts["FN"]) if counts["TP"] + counts["FN"] else None
        accepted = [row for row in rows if row["gpt_positive"]]
        deterministic = [row for row in rows if row["deterministic_positive"]]
        abstained = [row for row in rows if row["recommended_action"] == "ABSTAIN"]
        disagreements = [row for row in rows if row["disagreement"]]
        def expectancy(values: list[dict[str, Any]]) -> float | None:
            return round(sum(_safe_float(row["realized_r"]) for row in values) / len(values), 4) if values else None
        return {
            "sample_size": len(rows), "precision": precision, "recall": recall,
            "false_positives": counts["FP"], "false_negatives": counts["FN"],
            "true_positives": counts["TP"], "true_negatives": counts["TN"],
            "gpt_expectancy_r": expectancy(accepted),
            "deterministic_expectancy_r": expectancy(deterministic),
            "abstention_quality": (sum(not bool(row["actual_positive"]) for row in abstained) / len(abstained)
                                    if abstained else None),
            "disagreement_count": len(disagreements),
            "disagreement_profitability_r": expectancy(disagreements),
            "profitable_disagreements": sum(bool(row["profitable_disagreement"]) for row in disagreements),
            "opportunity_quality_positive": expectancy([{"realized_r": row["opportunity_quality"]}
                                                         for row in rows if row["actual_positive"]]),
            "opportunity_quality_negative": expectancy([{"realized_r": row["opportunity_quality"]}
                                                         for row in rows if not row["actual_positive"]]),
            "status": "SUFFICIENT" if len(rows) >= 30 else "INSUFFICIENT_SAMPLES",
        }

    def regime_report(self, identity_checksum: str | None = None,
                      telegram_id: int | None = None) -> dict[str, Any]:
        where, params = self._scope(identity_checksum, telegram_id)
        with connect() as conn:
            rows = [dict(row) for row in conn.execute(f"""SELECT e.*,i.market_regimes_json
                FROM ai_counterfactual_evaluations e
                JOIN ai_decisions d ON d.decision_id=e.decision_id
                LEFT JOIN ai_decision_intelligence i ON i.decision_id=e.decision_id {where}""",
                tuple(params)).fetchall()]
        result: dict[str, Any] = {}
        for regime in REGIMES + ("UNKNOWN",):
            values = [row for row in rows if regime in (
                _loads(row.get("market_regimes_json"), []) or [row["primary_regime"]])]
            if not values:
                continue
            result[regime] = {
                "samples": len(values),
                "accuracy": sum(bool(row["gpt_correct"]) for row in values) / len(values),
                "expectancy_r": round(sum(_safe_float(row["realized_r"]) for row in values) / len(values), 4),
                "abstentions": sum(not bool(row["gpt_positive"]) for row in values),
                "false_positives": sum(row["classification"] == "FP" for row in values),
                "false_negatives": sum(row["classification"] == "FN" for row in values),
            }
        return {"sample_size": len(rows), "regimes": result}

    def refresh_learning(self, identity_checksum: str | None = None) -> dict[str, Any]:
        where, params = self._scope(identity_checksum, None)
        with connect() as conn:
            rows = [dict(row) for row in conn.execute(f"""SELECT e.*,d.supporting_factors_json,
                    d.conflicting_factors_json,r.snapshot_json FROM ai_counterfactual_evaluations e
                    JOIN ai_decisions d ON d.decision_id=e.decision_id
                    JOIN research_signal_snapshots r ON r.signal_id=e.signal_id
                        AND r.capture_quality='DECISION_TIME'
                    {where} ORDER BY e.id""", tuple(params)).fetchall()]
        evidence: dict[str, list[float]] = defaultdict(list)
        indicators: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            realized = _safe_float(row["realized_r"])
            for factor in _loads(row.get("supporting_factors_json"), []):
                key = _normalized_factor(factor)
                if key:
                    evidence[key].append(realized)
            snapshot = _loads(row.get("snapshot_json"), {})
            for key, value in _feature_map(snapshot.get("features") if isinstance(snapshot, dict) else {}).items():
                if isinstance(value, bool):
                    label = f"{key}={str(value).lower()}"
                elif isinstance(value, str):
                    label = f"{key}={_normalized_factor(value)[:40]}"
                elif isinstance(value, (int, float)):
                    label = key
                else:
                    continue
                indicators[label].append(realized)
        def rank(source: dict[str, list[float]], minimum: int = 2) -> list[dict[str, Any]]:
            values = [{"factor": key, "samples": len(results),
                       "expectancy_r": round(sum(results) / len(results), 4),
                       "profitable_rate": round(sum(value > 0 for value in results) / len(results), 4)}
                      for key, results in source.items() if len(results) >= minimum]
            return sorted(values, key=lambda item: (-item["samples"], -item["expectancy_r"], item["factor"]))[:30]
        evidence_rank = rank(evidence)
        indicator_rank = rank(indicators, 3)
        failures = sorted((item for item in evidence_rank if item["expectancy_r"] < 0),
                          key=lambda item: (item["expectancy_r"], -item["samples"]))[:15]
        counter = self.counterfactual_report(identity_checksum)
        regimes = self.regime_report(identity_checksum)["regimes"]
        from services.ai_trading import checksum
        snapshot_key = checksum({"identity": identity_checksum, "samples": len(rows),
                                 "last": rows[-1]["decision_id"] if rows else None,
                                 "evidence": evidence_rank, "indicators": indicator_rank})
        with connect() as conn:
            conn.execute("""INSERT INTO ai_learning_snapshots(snapshot_key,identity_checksum,sample_size,
                expectancy_r,precision_score,recall_score,evidence_json,indicators_json,
                recurring_failures_json,regimes_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(snapshot_key) DO NOTHING""", (
                snapshot_key, identity_checksum, len(rows), counter["gpt_expectancy_r"],
                counter["precision"], counter["recall"], _json(evidence_rank), _json(indicator_rank),
                _json(failures), _json(regimes), _now()))
        return {"snapshot_key": snapshot_key, "sample_size": len(rows),
                "evidence": evidence_rank, "indicators": indicator_rank,
                "recurring_failures": failures, "regimes": regimes, "counterfactual": counter}

    @staticmethod
    def latest_learning(identity_checksum: str | None = None) -> dict[str, Any] | None:
        with connect() as conn:
            row = conn.execute("""SELECT * FROM ai_learning_snapshots
                WHERE (? IS NULL OR identity_checksum=?) ORDER BY id DESC LIMIT 1""",
                (identity_checksum, identity_checksum)).fetchone()
        if not row:
            return None
        result = dict(row)
        for key in ("evidence_json", "indicators_json", "recurring_failures_json", "regimes_json"):
            result[key] = _loads(result.get(key), [])
        return result

    @staticmethod
    def record_request_event(*, identity_checksum: str, signal_id: int | None,
                             attempt: int, status: str, reason_code: str,
                             latency_ms: float | None = None,
                             request_id: str | None = None) -> None:
        event_key = str(uuid.uuid4())
        with connect() as conn:
            conn.execute("""INSERT INTO ai_provider_request_events(event_key,identity_checksum,signal_id,
                attempt_number,status,reason_code,latency_ms,provider_request_id,created_at)
                VALUES(?,?,?,?,?,?,?,?,?)""", (event_key, identity_checksum, signal_id, attempt,
                status, reason_code, latency_ms, request_id, _now()))

    @staticmethod
    def record_queue(*, identity_checksum: str, queued: int, processed: int,
                     dropped: int, failed: int, cancelled: int, duration_ms: float) -> None:
        with connect() as conn:
            conn.execute("""INSERT INTO ai_observation_queue_snapshots(identity_checksum,queued,processed,
                dropped,failed,cancelled,duration_ms,created_at) VALUES(?,?,?,?,?,?,?,?)""",
                (identity_checksum, queued, processed, dropped, failed, cancelled, duration_ms, _now()))

    def provider_health(self, identity_checksum: str | None = None) -> dict[str, Any]:
        with connect() as conn:
            state = conn.execute("""SELECT * FROM ai_provider_state
                WHERE (? IS NULL OR identity_checksum=?) ORDER BY updated_at DESC LIMIT 1""",
                (identity_checksum, identity_checksum)).fetchone()
            events = [dict(row) for row in conn.execute("""SELECT * FROM ai_provider_request_events
                WHERE (? IS NULL OR identity_checksum=?) ORDER BY id DESC LIMIT 100""",
                (identity_checksum, identity_checksum)).fetchall()]
            queue = conn.execute("""SELECT * FROM ai_observation_queue_snapshots
                WHERE (? IS NULL OR identity_checksum=?) ORDER BY id DESC LIMIT 1""",
                (identity_checksum, identity_checksum)).fetchone()
        latencies = sorted(_safe_float(row.get("latency_ms")) for row in events if row.get("latency_ms") is not None)
        return {
            "circuit": dict(state) if state else None, "recent_events": len(events),
            "failures": sum(row["status"] == "FAILED" for row in events),
            "retries": sum(int(row.get("attempt_number") or 1) > 1 for row in events),
            "cancellations": sum(row["reason_code"] == "REQUEST_CANCELLED" for row in events),
            "p95_latency_ms": latencies[max(0, math.ceil(len(latencies) * .95) - 1)] if latencies else 0,
            "queue": dict(queue) if queue else None,
        }

    def dashboard(self, identity_checksum: str | None = None,
                  telegram_id: int | None = None) -> dict[str, Any]:
        clauses, params = [], []
        if identity_checksum:
            clauses.append("provider_identity_checksum=?")
            params.append(identity_checksum)
        if telegram_id is not None:
            clauses.append("telegram_id=?")
            params.append(telegram_id)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        with connect() as conn:
            row = conn.execute(f"""SELECT COUNT(*) decisions,SUM(CASE WHEN provider_invoked=1 THEN 1 ELSE 0 END) requests,
                SUM(CASE WHEN cache_hit=1 THEN 1 ELSE 0 END) cache_hits,
                SUM(CASE WHEN validation_code='VALID' THEN 1 ELSE 0 END) valid
                FROM ai_decisions {where}""", tuple(params)).fetchone()
        decisions = int(row["decisions"] or 0)
        cache_hits = int(row["cache_hits"] or 0)
        return {"decisions": decisions, "provider_requests": int(row["requests"] or 0),
                "cache_hits": cache_hits, "cache_hit_ratio": cache_hits / decisions if decisions else 0,
                "valid_rate": int(row["valid"] or 0) / decisions if decisions else 0,
                "counterfactual": self.counterfactual_report(identity_checksum, telegram_id),
                "regimes": self.regime_report(identity_checksum, telegram_id),
                "health": self.provider_health(identity_checksum),
                "learning": self.latest_learning(identity_checksum)}

    @staticmethod
    def history(telegram_id: int | None = None, limit: int = 10) -> list[dict[str, Any]]:
        with connect() as conn:
            rows = conn.execute("""SELECT d.*,i.market_regimes_json,i.similarity_summary_json,
                e.classification,e.realized_r evaluated_r FROM ai_decisions d
                LEFT JOIN ai_decision_intelligence i ON i.decision_id=d.decision_id
                LEFT JOIN ai_counterfactual_evaluations e ON e.decision_id=d.decision_id
                WHERE (? IS NULL OR d.telegram_id=?) ORDER BY d.id DESC LIMIT ?""",
                (telegram_id, telegram_id, max(1, min(int(limit), 25)))).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def similarities(signal_id: int, telegram_id: int | None = None) -> list[dict[str, Any]]:
        with connect() as conn:
            source = conn.execute("""SELECT decision_id FROM ai_decisions WHERE signal_id=?
                AND (? IS NULL OR telegram_id=?) ORDER BY id DESC LIMIT 1""",
                (signal_id, telegram_id, telegram_id)).fetchone()
            if not source:
                return []
            rows = conn.execute("""SELECT * FROM ai_decision_similarities
                WHERE source_decision_id=? ORDER BY similarity_score DESC LIMIT 10""",
                (source["decision_id"],)).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def startup_validate() -> dict[str, Any]:
        required = {"ai_decisions", "ai_decision_intelligence", "ai_counterfactual_evaluations",
                    "ai_learning_snapshots", "ai_provider_request_events"}
        try:
            backend = database_backend()
        except Exception as exc:
            return {"valid": False, "missing_tables": sorted(required),
                    "stale_request_claims_recovered": 0,
                    "stale_certification_claims_recovered": 0,
                    "ai_gated_execution_authority": False,
                    "failure_reason": "DATABASE_BACKEND_UNDETERMINED",
                    "failure_detail": type(exc).__name__}
        if backend not in {"sqlite", "postgresql"}:
            return {"valid": False, "missing_tables": sorted(required),
                    "stale_request_claims_recovered": 0,
                    "stale_certification_claims_recovered": 0,
                    "ai_gated_execution_authority": False,
                    "failure_reason": "DATABASE_BACKEND_UNDETERMINED",
                    "failure_detail": str(backend)}
        now = _now()
        with connect() as conn:
            if backend == "sqlite":
                names = {row["name"] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            else:
                names = {row["table_name"] for row in conn.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema='public'").fetchall()}
            stale_requests = conn.execute("DELETE FROM ai_request_claims WHERE expires_at<=?", (now,)).rowcount
            stale_certifications = conn.execute("DELETE FROM ai_certification_claims WHERE expires_at<=?", (now,)).rowcount
        missing = sorted(required - names)
        return {"valid": not missing, "missing_tables": missing,
                "stale_request_claims_recovered": max(0, stale_requests),
                "stale_certification_claims_recovered": max(0, stale_certifications),
                "ai_gated_execution_authority": False}
