from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from database.database import connect


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
    return {"sample_size": len(normalized), "brier_score": round(brier, 6),
            "expected_calibration_error": round(ece, 6), "reliability": rows}


class AIEvaluationService:
    def metrics(self, telegram_id: int | None = None) -> dict[str, Any]:
        where, params = ("WHERE d.telegram_id=?", (telegram_id,)) if telegram_id is not None else ("", ())
        with connect() as conn:
            rows = [dict(row) for row in conn.execute(f"""SELECT d.*,o.signal_result,o.direction_correct,
                o.intervention_type,o.realized_r,o.counterfactual_result,s.setup_key
                FROM ai_decisions d LEFT JOIN ai_decision_outcomes o ON o.decision_id=d.decision_id
                LEFT JOIN signals s ON s.id=d.signal_id
                {where} ORDER BY d.id""", params).fetchall()]
        count = len(rows)
        actions: dict[str, int] = {}
        for row in rows:
            action = str(row["recommended_action"])
            actions[action] = actions.get(action, 0) + 1
        resolved = [row for row in rows if row.get("direction_correct") is not None]
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
            "valid_schema_rate": sum(bool(row["schema_valid"]) for row in rows) / count if count else 0,
            "abstention_rate": sum(bool(row["abstention"]) for row in rows) / count if count else 0,
            "recommendations": actions,
            "average_latency_ms": sum(float(row["latency_ms"] or 0) for row in rows) / count if count else 0,
            "estimated_cost_usd": str(sum((__import__("decimal").Decimal(str(row["estimated_cost_usd"] or 0)) for row in rows), __import__("decimal").Decimal("0"))),
            "agreement_rate": len(agrees) / sum(row.get("deterministic_accepted") is not None for row in rows) if any(row.get("deterministic_accepted") is not None for row in rows) else None,
            "reject_precision": sum(not bool(row["direction_correct"]) for row in reject) / len(reject) if reject else None,
            "accept_precision": sum(bool(row["direction_correct"]) for row in accept) / len(accept) if accept else None,
            "calibration": calibration,
            "breakdowns": breakdowns,
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
