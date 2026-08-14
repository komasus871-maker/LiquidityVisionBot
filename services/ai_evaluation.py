from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any

from database.database import connect
from services.ai_trading import (SCHEMA_NOT_EVALUABLE_STAGES, schema_pipeline_valid)


ACCEPT_ACTIONS = {"ACCEPT_REDUCED", "ACCEPT_STANDARD"}


class AIOutcomeRepository:
    def attach(self, decision_id: str, **outcome: Any) -> dict[str, Any]:
        allowed = {
            "signal_result", "signal_mfe", "signal_mae", "direction_correct",
            "time_to_movement_seconds", "deterministic_result", "execution_result",
            "realized_pnl", "realized_r", "fees", "slippage_pct", "intervention_type",
            "intervention_delta_r", "hypothetical_result", "counterfactual_result",
        }
        values = {key: outcome.get(key) for key in allowed}
        now = datetime.now(timezone.utc).isoformat()
        with connect() as conn:
            decision = conn.execute("SELECT decision_id FROM ai_decisions WHERE decision_id=?", (decision_id,)).fetchone()
            if decision is None:
                raise KeyError(decision_id)
            conn.execute("""INSERT INTO ai_decision_outcomes(
                decision_id,signal_result,signal_mfe,signal_mae,direction_correct,time_to_movement_seconds,
                deterministic_result,execution_result,realized_pnl,realized_r,fees,slippage_pct,
                intervention_type,intervention_delta_r,hypothetical_result,counterfactual_result,attached_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(decision_id) DO UPDATE SET
                signal_result=excluded.signal_result,signal_mfe=excluded.signal_mfe,
                signal_mae=excluded.signal_mae,direction_correct=excluded.direction_correct,
                time_to_movement_seconds=excluded.time_to_movement_seconds,
                deterministic_result=excluded.deterministic_result,execution_result=excluded.execution_result,
                realized_pnl=excluded.realized_pnl,realized_r=excluded.realized_r,fees=excluded.fees,
                slippage_pct=excluded.slippage_pct,intervention_type=excluded.intervention_type,
                intervention_delta_r=excluded.intervention_delta_r,hypothetical_result=excluded.hypothetical_result,
                counterfactual_result=excluded.counterfactual_result,attached_at=excluded.attached_at""", (
                decision_id, values["signal_result"], values["signal_mfe"], values["signal_mae"],
                None if values["direction_correct"] is None else int(bool(values["direction_correct"])),
                values["time_to_movement_seconds"], values["deterministic_result"], values["execution_result"],
                values["realized_pnl"], values["realized_r"], values["fees"], values["slippage_pct"],
                values["intervention_type"], values["intervention_delta_r"], values["hypothetical_result"],
                values["counterfactual_result"], now,
            ))
            row = conn.execute("SELECT * FROM ai_decision_outcomes WHERE decision_id=?", (decision_id,)).fetchone()
        return dict(row)

    def attach_closed_signals(self, limit: int = 100) -> int:
        """Attach signal quality independently from execution/intervention outcomes."""
        with connect() as conn:
            rows = conn.execute(f"""SELECT d.decision_id,s.result,s.max_profit_pct,s.max_drawdown_pct
                FROM ai_decisions d JOIN signals s ON s.id=d.signal_id
                LEFT JOIN ai_decision_outcomes o ON o.decision_id=d.decision_id
                WHERE o.decision_id IS NULL AND s.result IS NOT NULL ORDER BY d.id LIMIT {max(1,min(limit,500))}""").fetchall()
        for row in rows:
            result = str(row["result"] or "").upper()
            intervention = result if result in {"MANUAL_STOP", "MANUAL_CLOSE", "PANIC_CLOSE", "EARLY_CLOSE"} else None
            positive = result in {"WIN", "TP1", "TP2", "TP3"}
            negative = result in {"LOSS", "STOP", "STOPPED", "SL"}
            self.attach(
                row["decision_id"], signal_result=result if positive or negative else None,
                signal_mfe=row["max_profit_pct"], signal_mae=row["max_drawdown_pct"],
                direction_correct=positive if positive or negative else None,
                execution_result=result if intervention else None, intervention_type=intervention,
            )
        return len(rows)


def calibration_metrics(samples: list[tuple[float, int]], buckets: int = 10) -> dict[str, Any]:
    if not samples:
        return {"sample_size": 0, "brier_score": None, "expected_calibration_error": None, "reliability": []}
    normalized = [(max(0.0, min(1.0, float(p))), int(bool(y))) for p, y in samples]
    brier = sum((p - y) ** 2 for p, y in normalized) / len(normalized)
    rows, ece = [], 0.0
    for index in range(buckets):
        low, high = index / buckets, (index + 1) / buckets
        values = [(p, y) for p, y in normalized if low <= p < high or (index == buckets - 1 and p == 1)]
        if not values:
            continue
        confidence = sum(p for p, _ in values) / len(values)
        accuracy = sum(y for _, y in values) / len(values)
        ece += len(values) / len(normalized) * abs(confidence - accuracy)
        rows.append({"bucket": f"{low:.1f}-{high:.1f}", "count": len(values),
                     "confidence": round(confidence, 4), "accuracy": round(accuracy, 4)})
    positives = sum(y for _, y in normalized)
    probability = positives / len(normalized)
    margin = 1.96 * math.sqrt(probability * (1 - probability) / len(normalized))
    return {"sample_size": len(normalized), "brier_score": round(brier, 6),
            "expected_calibration_error": round(ece, 6), "reliability": rows,
            "accuracy_confidence_interval_95": [round(max(0, probability - margin), 4),
                                                round(min(1, probability + margin), 4)]}


class AIEvaluationService:
    @staticmethod
    def _percentile(values: list[float], percentile: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        return ordered[max(0, math.ceil(len(ordered) * percentile) - 1)]

    def rolling_metrics(self, telegram_id: int | None = None,
                        identity_checksum: str | None = None) -> dict[str, Any]:
        windows = {"1h": timedelta(hours=1), "24h": timedelta(hours=24),
                   "7d": timedelta(days=7), "30d": timedelta(days=30)}
        result: dict[str, Any] = {}
        for label, delta in windows.items():
            clauses, params = ["created_at>=?"], [(datetime.now(timezone.utc) - delta).isoformat()]
            if telegram_id is not None:
                clauses.append("telegram_id=?")
                params.append(telegram_id)
            if identity_checksum is not None:
                clauses.append("provider_identity_checksum=?")
                params.append(identity_checksum)
                clauses.append("provider_invoked=1")
            with connect() as conn:
                rows = [dict(row) for row in conn.execute(
                    f"SELECT * FROM ai_decisions WHERE {' AND '.join(clauses)} ORDER BY id", tuple(params)).fetchall()]
            count = len(rows)
            codes = [str(row.get("validation_code") or "") for row in rows]
            latencies = [float(row.get("latency_ms") or 0) for row in rows]
            result[label] = {
                "requests": count,
                "http_success_rate": sum(not code.startswith("AI_PROVIDER_HTTP_") and code not in
                    {"PROVIDER_TIMEOUT", "PROVIDER_TRANSPORT_ERROR", "PROVIDER_RATE_LIMIT"} for code in codes) / count if count else 0,
                "schema_success_rate": sum(bool(row.get("provider_invoked")) and
                                           schema_pipeline_valid(row.get("validation_stage"))
                                           for row in rows) / count if count else 0,
                "semantic_success_rate": codes.count("VALID") / count if count else 0,
                "abstention_rate": sum(bool(row.get("abstention")) for row in rows) / count if count else 0,
                "timeout_count": codes.count("PROVIDER_TIMEOUT"),
                "rate_limit_count": codes.count("PROVIDER_RATE_LIMIT"),
                "server_error_count": sum(code.startswith("AI_PROVIDER_HTTP_5") for code in codes),
                "latency_ms": {"p50": self._percentile(latencies, .5), "p95": self._percentile(latencies, .95),
                               "p99": self._percentile(latencies, .99)},
                "input_tokens": sum(int(row.get("input_tokens") or 0) for row in rows),
                "output_tokens": sum(int(row.get("output_tokens") or 0) for row in rows),
                "cached_tokens": sum(int(row.get("cached_tokens") or 0) for row in rows),
                "cache_write_tokens": sum(int(row.get("cache_write_tokens") or 0) for row in rows),
                "reasoning_tokens": sum(int(row.get("reasoning_tokens") or 0) for row in rows),
                "cost_usd": str(sum((__import__("decimal").Decimal(str(row.get("estimated_cost_usd") or 0)) for row in rows), __import__("decimal").Decimal("0"))),
                "request_ids_present": sum(bool(row.get("provider_request_id")) for row in rows),
                "downgrade_count": sum(bool(row.get("downgrade_reason")) for row in rows),
                "duplicate_suppression": self._observation_count(identity_checksum, "DUPLICATE_SUPPRESSED",
                                                                  datetime.now(timezone.utc) - delta),
            }
        return result

    @staticmethod
    def _observation_count(identity_checksum: str | None, reason_code: str,
                           since: datetime | None = None) -> int:
        clauses, params = ["reason_code=?"], [reason_code]
        if identity_checksum is not None:
            clauses.append("identity_checksum=?")
            params.append(identity_checksum)
        if since is not None:
            clauses.append("created_at>=?")
            params.append(since.isoformat())
        with connect() as conn:
            row = conn.execute(f"SELECT COUNT(*) n FROM ai_observation_events WHERE {' AND '.join(clauses)}",
                               tuple(params)).fetchone()
        return int(row["n"] or 0)

    def quality_report(self, telegram_id: int | None = None, minimum_samples: int = 30,
                       identity_checksum: str | None = None) -> dict[str, Any]:
        metrics = self.metrics(telegram_id, identity_checksum)
        with connect() as conn:
            clauses, params = [], []
            if telegram_id is not None:
                clauses.append("d.telegram_id=?")
                params.append(telegram_id)
            if identity_checksum is not None:
                clauses.append("d.provider_identity_checksum=?")
                params.append(identity_checksum)
                clauses.append("d.provider_invoked=1")
            where = "WHERE " + " AND ".join(clauses) if clauses else ""
            rows = [dict(row) for row in conn.execute(f"""SELECT d.recommended_action,d.abstention,
                d.deterministic_accepted,o.direction_correct,o.realized_r,o.intervention_type,o.counterfactual_result
                FROM ai_decisions d LEFT JOIN ai_decision_outcomes o ON o.decision_id=d.decision_id {where}""", tuple(params)).fetchall()]
        resolved = [row for row in rows if row.get("direction_correct") is not None and not row.get("intervention_type")]
        accepted = [row for row in resolved if row["recommended_action"] in ACCEPT_ACTIONS]
        rejected = [row for row in resolved if row["recommended_action"] == "REJECT"]
        abstained = [row for row in resolved if bool(row["abstention"])]
        def quality(group: list[dict[str, Any]]) -> dict[str, Any]:
            r_values = [float(x["realized_r"]) for x in group if x.get("realized_r") is not None]
            wins = [value for value in r_values if value > 0]
            losses = [value for value in r_values if value < 0]
            equity = peak = drawdown = 0.0
            for value in r_values:
                equity += value
                peak = max(peak, equity)
                drawdown = min(drawdown, equity - peak)
            win_rate = sum(bool(x["direction_correct"]) for x in group) / len(group) if group else None
            margin = 1.96 * math.sqrt(win_rate * (1 - win_rate) / len(group)) if group else None
            return {"sample_size": len(group), "win_rate": win_rate,
                    "win_rate_confidence_interval_95": None if margin is None else
                    [max(0, win_rate - margin), min(1, win_rate + margin)],
                    "expectancy_r": sum(r_values) / len(r_values) if r_values else None,
                    "profit_factor": sum(wins) / abs(sum(losses)) if losses else None,
                    "max_drawdown_r": drawdown if r_values else None,
                    "status": "SUFFICIENT" if len(group) >= minimum_samples else "INSUFFICIENT_SAMPLES"}
        counterfactual = {
            "ai_accept": quality(accepted), "ai_reject": quality(rejected), "ai_abstain": quality(abstained),
            "deterministic_accept": quality([x for x in resolved if bool(x.get("deterministic_accepted"))]),
            "deterministic_reject": quality([x for x in resolved if x.get("deterministic_accepted") == 0]),
        }
        return {"metrics": metrics, "rolling": self.rolling_metrics(telegram_id, identity_checksum),
                "counterfactual": counterfactual,
                "abstention_quality": {"sample_size": len(abstained),
                    "correct_abstentions": sum(not bool(x["direction_correct"]) for x in abstained),
                    "false_abstentions_or_missed_opportunities": sum(bool(x["direction_correct"]) for x in abstained),
                    "status": "SUFFICIENT" if len(abstained) >= minimum_samples else "INSUFFICIENT_SAMPLES"},
                "manual_interventions_excluded_from_calibration": sum(bool(x.get("intervention_type")) for x in rows),
                "warning": "No improvement claim is valid when a cohort reports INSUFFICIENT_SAMPLES."}

    def drift(self, telegram_id: int | None = None, minimum_samples: int = 30,
              identity_checksum: str | None = None) -> dict[str, Any]:
        rolling = self.rolling_metrics(telegram_id, identity_checksum)
        current, baseline = rolling["24h"], rolling["30d"]
        base_scope = f"telegram:{telegram_id}" if telegram_id is not None else "global"
        scope = f"{base_scope}:identity:{identity_checksum}" if identity_checksum else base_scope
        with connect() as conn:
            saved = conn.execute("SELECT * FROM ai_drift_baselines WHERE scope_key=? ORDER BY id DESC LIMIT 1", (scope,)).fetchone()
        if saved:
            baseline = json.loads(saved["metrics_json"])
        if current["requests"] < minimum_samples or baseline["requests"] < minimum_samples:
            return {"status": "INSUFFICIENT_SAMPLES", "current_samples": current["requests"],
                    "baseline_samples": baseline["requests"], "alerts": []}
        alerts = []
        for key in ("schema_success_rate", "semantic_success_rate"):
            delta = current[key] - baseline[key]
            if delta < -0.1:
                alerts.append({"metric": key, "delta": round(delta, 4), "severity": "HIGH"})
        latency_delta = current["latency_ms"]["p95"] - baseline["latency_ms"]["p95"]
        if baseline["latency_ms"]["p95"] and latency_delta / baseline["latency_ms"]["p95"] > .5:
            alerts.append({"metric": "p95_latency_ms", "delta": round(latency_delta, 3), "severity": "MEDIUM"})
        return {"status": "DRIFT_DETECTED" if alerts else "STABLE", "alerts": alerts,
                "current_samples": current["requests"], "baseline_samples": baseline["requests"]}

    def capture_drift_baseline(self, identity_checksum: str, telegram_id: int | None = None,
                               minimum_samples: int = 30) -> dict[str, Any]:
        base_scope = f"telegram:{telegram_id}" if telegram_id is not None else "global"
        scope = f"{base_scope}:identity:{identity_checksum}"
        baseline = self.rolling_metrics(telegram_id, identity_checksum)["30d"]
        status = "CAPTURED" if baseline["requests"] >= minimum_samples else "INSUFFICIENT_SAMPLES"
        if status == "CAPTURED":
            with connect() as conn:
                conn.execute("""INSERT INTO ai_drift_baselines(identity_checksum,scope_key,sample_size,
                    metrics_json,created_at) VALUES(?,?,?,?,?) ON CONFLICT(identity_checksum,scope_key)
                    DO UPDATE SET sample_size=excluded.sample_size,metrics_json=excluded.metrics_json,
                    created_at=excluded.created_at""", (identity_checksum, scope, baseline["requests"],
                    json.dumps(baseline, sort_keys=True), datetime.now(timezone.utc).isoformat()))
        return {"status": status, "scope": scope, "sample_size": baseline["requests"]}

    def metrics(self, telegram_id: int | None = None,
                identity_checksum: str | None = None) -> dict[str, Any]:
        clauses, params = [], []
        if telegram_id is not None:
            clauses.append("d.telegram_id=?")
            params.append(telegram_id)
        if identity_checksum is not None:
            clauses.append("d.provider_identity_checksum=?")
            params.append(identity_checksum)
            clauses.append("d.provider_invoked=1")
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        with connect() as conn:
            rows = [dict(row) for row in conn.execute(f"""SELECT d.*,o.signal_result,o.direction_correct,
                o.intervention_type,o.realized_r,o.counterfactual_result,s.setup_key
                FROM ai_decisions d LEFT JOIN ai_decision_outcomes o ON o.decision_id=d.decision_id
                LEFT JOIN signals s ON s.id=d.signal_id
                {where} ORDER BY d.id""", tuple(params)).fetchall()]
        count = len(rows)
        actions: dict[str, int] = {}
        for row in rows:
            action = str(row["recommended_action"])
            actions[action] = actions.get(action, 0) + 1
        resolved = [row for row in rows if row.get("direction_correct") is not None]
        schema_valid_count = sum(bool(row.get("provider_invoked")) and
                                 schema_pipeline_valid(row.get("validation_stage")) for row in rows)
        schema_not_evaluable_count = sum(
            not bool(row.get("provider_invoked")) or
            str(row.get("validation_stage") or "") in SCHEMA_NOT_EVALUABLE_STAGES for row in rows)
        structural_schema_invalid_count = sum(
            str(row.get("validation_stage") or "") == "JSON_SCHEMA_VALIDATION" for row in rows)
        schema_evaluable_count = count - schema_not_evaluable_count
        structural_schema_valid_count = schema_evaluable_count - structural_schema_invalid_count
        semantic_valid_count = sum(str(row.get("validation_code") or "") == "VALID" for row in rows)
        transport_codes = {"PROVIDER_TIMEOUT", "PROVIDER_TRANSPORT_ERROR", "PROVIDER_FAILURE", "PROVIDER_RATE_LIMIT"}
        transport_failure_count = sum(str(row.get("validation_code") or "") in transport_codes or
                                      str(row.get("validation_code") or "").startswith("AI_PROVIDER_HTTP_") for row in rows)
        latencies = sorted(float(row["latency_ms"] or 0) for row in rows)
        p95_index = max(0, math.ceil(len(latencies) * 0.95) - 1) if latencies else 0
        samples = [(float(row["raw_confidence"]) / 100, int(row["direction_correct"])) for row in resolved]
        calibration = calibration_metrics(samples)
        agrees = [row for row in rows if row.get("deterministic_accepted") is not None and
                  (bool(row["deterministic_accepted"]) == (row["recommended_action"] in ACCEPT_ACTIONS))]
        reject = [row for row in resolved if row["recommended_action"] == "REJECT"]
        accept = [row for row in resolved if row["recommended_action"] in ACCEPT_ACTIONS]
        dimensions = {
            "regime": "regime", "symbol": "symbol", "timeframe": "timeframe",
            "setup_family": "setup_key", "prompt_version": "prompt_version", "model_version": "model_version",
        }
        breakdowns: dict[str, dict[str, int]] = {}
        for label, field in dimensions.items():
            values: dict[str, int] = {}
            for row in rows:
                key = str(row.get(field) or "UNKNOWN")
                values[key] = values.get(key, 0) + 1
            breakdowns[label] = values
        return {
            "decision_count": count,
            "schema_valid_count": schema_valid_count,
            "schema_evaluable_count": schema_evaluable_count,
            "schema_not_evaluable_count": schema_not_evaluable_count,
            "structural_schema_valid_count": structural_schema_valid_count,
            "structural_schema_invalid_count": structural_schema_invalid_count,
            "structural_schema_valid_rate": (
                structural_schema_valid_count / schema_evaluable_count if schema_evaluable_count else 0),
            "semantic_valid_count": semantic_valid_count,
            "valid_schema_rate": schema_valid_count / count if count else 0,
            "semantic_valid_rate": semantic_valid_count / count if count else 0,
            "abstention_rate": sum(bool(row["abstention"]) for row in rows) / count if count else 0,
            "recommendations": actions,
            "average_latency_ms": sum(float(row["latency_ms"] or 0) for row in rows) / count if count else 0,
            "p95_latency_ms": latencies[p95_index] if latencies else 0,
            "estimated_cost_usd": str(sum((__import__("decimal").Decimal(str(row["estimated_cost_usd"] or 0)) for row in rows), __import__("decimal").Decimal("0"))),
            "input_tokens": sum(int(row.get("input_tokens") or 0) for row in rows),
            "output_tokens": sum(int(row.get("output_tokens") or 0) for row in rows),
            "cached_tokens": sum(int(row.get("cached_tokens") or 0) for row in rows),
            "cache_write_tokens": sum(int(row.get("cache_write_tokens") or 0) for row in rows),
            "reasoning_tokens": sum(int(row.get("reasoning_tokens") or 0) for row in rows),
            "cost_status": "UNPRICED" if not rows or any(str(row.get("cost_status") or "UNPRICED") == "UNPRICED" for row in rows) else "PRICED",
            "downgrade_count": sum(bool(row.get("downgrade_reason")) for row in rows),
            "timeout_rate": sum(row.get("validation_code") == "PROVIDER_TIMEOUT" for row in rows) / count if count else 0,
            "rate_limit_rate": sum(row.get("validation_code") == "PROVIDER_RATE_LIMIT" for row in rows) / count if count else 0,
            "transport_failure_count": transport_failure_count,
            "transport_failure_rate": transport_failure_count / count if count else 0,
            "timeout_count": sum(row.get("validation_code") == "PROVIDER_TIMEOUT" for row in rows),
            "rate_limit_count": sum(row.get("validation_code") == "PROVIDER_RATE_LIMIT" for row in rows),
            "server_error_count": sum(str(row.get("validation_code") or "").startswith("AI_PROVIDER_HTTP_5") for row in rows),
            "duplicates_suppressed": self._observation_count(identity_checksum, "DUPLICATE_SUPPRESSED"),
            "queue_drops": self._observation_count(identity_checksum, "OBSERVATION_QUEUE_FULL"),
            "resolved_outcomes": len(resolved),
            "current_governance_sample_count": count,
            "request_ids_available": sum(bool(row.get("provider_request_id")) for row in rows),
            "compatibility_failures": sum(row.get("validation_code") in {"PROVIDER_CAPABILITY_MISMATCH", "MODEL_PARAMETER_UNSUPPORTED"} for row in rows),
            "agreement_rate": len(agrees) / sum(row.get("deterministic_accepted") is not None for row in rows) if any(row.get("deterministic_accepted") is not None for row in rows) else None,
            "reject_precision": sum(not bool(row["direction_correct"]) for row in reject) / len(reject) if reject else None,
            "accept_precision": sum(bool(row["direction_correct"]) for row in accept) / len(accept) if accept else None,
            "calibration": calibration,
            "breakdowns": breakdowns,
            "scope": "CURRENT_PROVIDER_IDENTITY" if identity_checksum else "GLOBAL_HISTORY",
            "identity_checksum": identity_checksum,
            "legacy_classification": {
                "legacy_disabled": sum(row.get("legacy_classification") == "LEGACY_DISABLED" or
                    (row.get("provider") == "disabled" and not row.get("provider_identity_checksum")) for row in rows),
                "legacy_unscoped": sum(row.get("legacy_classification") == "LEGACY_UNSCOPED" or
                    (row.get("provider") != "disabled" and not row.get("provider_identity_checksum")) for row in rows),
                "identity_scoped": sum(row.get("legacy_classification") == "CURRENT_IDENTITY" or
                    bool(row.get("provider_identity_checksum")) for row in rows),
            },
            "warning": "Raw AI confidence is not a calibrated probability.",
        }

    def snapshot_calibration(self, *, scope_key: str = "global", model_version: str = "",
                             minimum_samples: int = 100) -> dict[str, Any]:
        with connect() as conn:
            rows = conn.execute("""SELECT d.raw_confidence,o.direction_correct FROM ai_decisions d
                JOIN ai_decision_outcomes o ON o.decision_id=d.decision_id
                WHERE o.direction_correct IS NOT NULL AND (?='' OR d.model_version=?)""",
                (model_version, model_version)).fetchall()
        result = calibration_metrics([(float(row["raw_confidence"]) / 100, int(row["direction_correct"])) for row in rows])
        status = "RELIABLE" if result["sample_size"] >= max(1, minimum_samples) else "INSUFFICIENT_SAMPLES"
        now = datetime.now(timezone.utc).isoformat()
        with connect() as conn:
            conn.execute("""INSERT INTO ai_calibration_snapshots(scope_key,model_version,sample_size,brier_score,
                expected_calibration_error,reliability_status,reliability_json,created_at)
                VALUES(?,?,?,?,?,?,?,?)""", (scope_key, model_version or "unknown", result["sample_size"],
                result["brier_score"], result["expected_calibration_error"], status,
                json.dumps(result["reliability"], sort_keys=True), now))
        return {**result, "reliability_status": status}
