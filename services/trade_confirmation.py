"""Descriptive pre-trade consistency checks with zero execution authority.

The engine may explain execution conditions for a decision already produced by
DecisionQualityEngine.  It never changes, replaces, approves, records, or
dispatches that decision.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import math
import statistics
from typing import Any


class ConfirmationState(StrEnum):
    CONFIRM = "CONFIRM"
    CAUTION = "CAUTION"
    REJECT = "REJECT"


@dataclass(frozen=True)
class ConfirmationResult:
    state: ConfirmationState
    grade: str
    confirmations: tuple[str, ...]
    warnings: tuple[str, ...]
    reason_codes: tuple[str, ...]
    inputs_available: tuple[str, ...]
    source_decision_action: str
    production_gate: bool = False
    economic_authority: bool = False
    version: str = "meta-confirmation-shadow-v1"

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value, "grade": self.grade,
            "confirmations": list(self.confirmations), "warnings": list(self.warnings),
            "reason_codes": list(self.reason_codes), "inputs_available": list(self.inputs_available),
            "source_decision_action": self.source_decision_action,
            "production_gate": self.production_gate, "economic_authority": self.economic_authority,
            "version": self.version,
        }


class TradeConfirmationEngine:
    """Evaluate current consistency without mutating the source analysis."""

    def evaluate(self, analysis: dict[str, Any], market: dict[str, Any] | None = None,
                 *, execution_health: str = "HEALTHY") -> ConfirmationResult:
        state = dict(market or {})
        source_action = str((analysis.get("unified_decision") or {}).get("action")
                            or analysis.get("decision_action") or analysis.get("recommendation") or "UNKNOWN")
        reasons: list[str] = []
        confirms: list[str] = []
        warnings: list[str] = []
        available: list[str] = []
        hard = 0
        caution = 0

        quality = analysis.get("decision_quality") or analysis.get("quality") or {}
        data_confidence = quality.get("data_confidence") if isinstance(quality, dict) else None
        if data_confidence is not None:
            available.append("market_data_quality")
            if float(data_confidence) < 40:
                reasons.append("DATA_QUALITY_INSUFFICIENT"); hard += 1
            elif float(data_confidence) < 65:
                warnings.append("market-data confidence is limited"); caution += 1
            else:
                confirms.append("market-data quality")

        checks = (
            ("spread_pct", .20, .08, "SPREAD_TOO_WIDE", "spread elevated", "spread"),
            ("expected_slippage_pct", .30, .12, "SLIPPAGE_TOO_HIGH", "expected slippage elevated", "liquidity"),
            ("cross_venue_diff_pct", .35, .15, "CROSS_VENUE_DISAGREEMENT", "cross-venue dispersion", "cross-venue agreement"),
        )
        for key, reject_at, warn_at, reason, warning, confirmation in checks:
            value = state.get(key)
            if value is None:
                continue
            available.append(key)
            magnitude = abs(float(value))
            if magnitude >= reject_at:
                reasons.append(reason); hard += 1
            elif magnitude >= warn_at:
                warnings.append(warning); caution += 1
            else:
                confirms.append(confirmation)

        imbalance = state.get("taker_imbalance")
        direction = str(analysis.get("direction") or "").upper()
        if imbalance is not None and direction in {"LONG", "SHORT", "BUY", "SELL"}:
            available.append("order_flow")
            aligned = float(imbalance) >= 0 if direction in {"LONG", "BUY"} else float(imbalance) <= 0
            if aligned:
                confirms.append("order flow")
            elif abs(float(imbalance)) >= .35:
                reasons.append("ORDER_FLOW_CONFLICT"); hard += 1
            else:
                warnings.append("order flow is mixed"); caution += 1

        funding = state.get("funding_rate")
        if funding is not None:
            available.append("funding")
            crowded = (direction in {"LONG", "BUY"} and float(funding) >= .001) or (
                direction in {"SHORT", "SELL"} and float(funding) <= -.001)
            if crowded:
                warnings.append("funding is crowded"); reasons.append("FUNDING_CROWDING"); caution += 1
            else:
                confirms.append("funding not extreme")

        duplicate_exposure = bool(state.get("duplicate_exposure"))
        portfolio_correlation = state.get("portfolio_correlation")
        if duplicate_exposure:
            available.append("portfolio_exposure")
            reasons.append("DUPLICATE_EXPOSURE"); hard += 1
        elif portfolio_correlation is not None:
            available.append("portfolio_correlation")
            if abs(float(portfolio_correlation)) >= .85:
                warnings.append("portfolio correlation is high"); reasons.append("CORRELATED_EXPOSURE"); caution += 1
            else:
                confirms.append("portfolio overlap acceptable")

        if execution_health.upper() != "HEALTHY":
            available.append("execution_health")
            reasons.append("EXECUTION_HEALTH_DEGRADED"); hard += 1
        else:
            confirms.append("execution health")

        if hard:
            result_state, grade = ConfirmationState.REJECT, "D"
        elif caution >= 2:
            result_state, grade = ConfirmationState.CAUTION, "C+"
        elif caution:
            result_state, grade = ConfirmationState.CAUTION, "B"
        else:
            result_state, grade = ConfirmationState.CONFIRM, "A" if len(confirms) >= 5 else "B+"
        return ConfirmationResult(
            state=result_state, grade=grade, confirmations=tuple(dict.fromkeys(confirms)),
            warnings=tuple(dict.fromkeys(warnings)), reason_codes=tuple(dict.fromkeys(reasons)),
            inputs_available=tuple(dict.fromkeys(available)), source_decision_action=source_action,
        )


META_CONFIRMATION_SPEC = {
    "version": "meta-confirmation-shadow-v1",
    "mode": "SHADOW_RESEARCH_ONLY",
    "evidence_start": "FIRST_EVENT_AFTER_EXPLICIT_REGISTRATION",
    "production_gate": False,
    "economic_authority": False,
    "cost_metric": "net_r_after_recorded_costs",
}
META_CONFIRMATION_EXPERIMENT_ID = hashlib.sha256(
    json.dumps(META_CONFIRMATION_SPEC, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()[:16]


class MetaGateResearch:
    """Paired descriptive evaluator for newly identified meta-gate evidence.

    Records must already contain immutable base outcome and confirmation state.
    The evaluator does not call the confirmation engine retrospectively, which
    prevents post-outcome reconstruction from masquerading as forward evidence.
    """

    experiment_id = META_CONFIRMATION_EXPERIMENT_ID
    evidence_start_semantics = META_CONFIRMATION_SPEC["evidence_start"]
    production_gate = False

    @staticmethod
    def _metrics(values: list[float]) -> dict[str, Any]:
        wins = [value for value in values if value > 0]
        losses = [value for value in values if value < 0]
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        equity = peak = drawdown = 0.0
        for value in values:
            equity += value
            peak = max(peak, equity)
            drawdown = min(drawdown, equity - peak)
        return {
            "n": len(values),
            "net_expectancy_r": round(statistics.mean(values), 6) if values else None,
            "profit_factor": round(gross_profit / gross_loss, 6) if gross_loss else None,
            "max_drawdown_r": round(drawdown, 6),
            "win_rate": round(len(wins) / len(values), 6) if values else None,
        }

    def compare(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        eligible = [record for record in records
                    if record.get("experiment_id") == self.experiment_id
                    and record.get("evidence_phase") == "FORWARD"
                    and isinstance(record.get("net_r_after_recorded_costs"), (int, float))
                    and math.isfinite(float(record["net_r_after_recorded_costs"]))]
        base = [float(record["net_r_after_recorded_costs"]) for record in eligible]
        gated = [float(record["net_r_after_recorded_costs"]) for record in eligible
                 if str(record.get("confirmation_state")) != ConfirmationState.REJECT.value]
        return {
            "experiment_id": self.experiment_id,
            "evidence_start_semantics": self.evidence_start_semantics,
            "base": self._metrics(base), "base_plus_meta_gate": self._metrics(gated),
            "rejected": len(base) - len(gated),
            "retention_rate": round(len(gated) / len(base), 6) if base else None,
            "qualified_for_production": False,
            "qualification_reason": "DESCRIPTIVE_SHADOW_ONLY",
            "economic_authority": False,
        }


def current_market_inputs(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Reduce bounded forward current state; never reads raw evidence."""
    if not rows:
        return {}
    freshest = max(rows, key=lambda row: row.get("observed_at") or "")
    snapshot = freshest.get("snapshot") or {}
    book = snapshot.get("book") or {}
    flow = ((snapshot.get("trade_flow") or {}).get("horizons_ms") or {}).get("60000") or {}
    derivatives = snapshot.get("derivatives") or {}
    cross = snapshot.get("cross_venue") or {}
    spread = book.get("spread_bps")
    dispersion = cross.get("dispersion_bps")
    return {
        "spread_pct": float(spread) / 100 if spread is not None else None,
        "expected_slippage_pct": None,
        "cross_venue_diff_pct": float(dispersion) / 100 if dispersion is not None else None,
        "taker_imbalance": flow.get("normalized_delta"),
        "book_imbalance": book.get("depth_imbalance"),
        "funding_rate": derivatives.get("funding_rate"),
        "oi_change_pct": derivatives.get("open_interest_change"),
    }


def render_quality_card(result: ConfirmationResult) -> str:
    lines = ["<b>Trade Quality · SHADOW CONFIRMATION</b>", f"Grade: <b>{result.grade}</b>",
             f"State: <b>{result.state.value}</b>"]
    if result.confirmations:
        lines += ["", "<b>Confirmations</b>", *(f"✓ {item}" for item in result.confirmations)]
    if result.warnings:
        lines += ["", "<b>Warnings</b>", *(f"⚠ {item}" for item in result.warnings)]
    if result.reason_codes:
        lines += ["", "Reason codes: <code>" + ", ".join(result.reason_codes) + "</code>"]
    lines += ["", "This descriptive SHADOW check cannot alter the DecisionQuality result or submit orders."]
    return "\n".join(lines)
