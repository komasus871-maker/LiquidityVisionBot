"""Frozen forward candidates and zero-authority Shadow research mechanics."""
from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from services.forward_event_store import AppendOnlyEventStore, canonical_json


FORWARD_PROGRAM_ID = "forward-microstructure-alpha-v1"
LATENCY_SCENARIOS_MS = {"LOW_LATENCY": 50, "NORMAL_LATENCY": 250, "STRESS_LATENCY": 1_000}
LABEL_HORIZONS_MS = (1_000, 5_000, 15_000, 30_000, 60_000, 300_000, 900_000, 1_800_000, 3_600_000)


@dataclass(frozen=True)
class ForwardCandidate:
    family: str
    variant: str
    parameters: dict[str, Any]
    required_features: tuple[str, ...]
    version: int = 1

    @property
    def candidate_id(self) -> str:
        return hashlib.sha256(canonical_json({
            "program": FORWARD_PROGRAM_ID,
            **asdict(self),
            "mode": "SHADOW_ONLY",
            "latency_scenarios_ms": LATENCY_SCENARIOS_MS,
        }).encode("utf-8")).hexdigest()[:16]


def frozen_forward_candidates() -> tuple[ForwardCandidate, ...]:
    definitions = (
        ("M1_FLOW_BOOK_MOMENTUM", ("trade_flow", "book"), {
            "STANDARD": {"delta_5s": .20, "book_imbalance": .15, "max_spread_bps": 4.0},
            "STRICT": {"delta_5s": .30, "book_imbalance": .25, "max_spread_bps": 3.0},
        }),
        ("M2_ABSORPTION_REVERSAL", ("trade_flow", "book", "price_progress"), {
            "STANDARD": {"delta_30s": .30, "max_progress_bps": 2.0, "opposite_depth_imbalance": .10},
            "STRICT": {"delta_30s": .40, "max_progress_bps": 1.0, "opposite_depth_imbalance": .20},
        }),
        ("M3_LIQUIDATION_EXHAUSTION", ("liquidations", "trade_flow", "open_interest"), {
            "STANDARD": {"liquidation_60s_usd": 250_000, "flow_exhaustion_abs": .10, "oi_drop": 0.0},
            "STRICT": {"liquidation_60s_usd": 1_000_000, "flow_exhaustion_abs": .05, "oi_drop": 0.0},
        }),
        ("M4_LIQUIDITY_VACUUM_BREAKOUT", ("trade_flow", "book"), {
            "STANDARD": {"near_depth_usd": 50_000, "delta_5s": .15, "max_spread_bps": 5.0},
            "STRICT": {"near_depth_usd": 20_000, "delta_5s": .25, "max_spread_bps": 4.0},
        }),
        ("M5_CROSS_VENUE_LEAD_LAG", ("cross_venue", "trade_flow", "book"), {
            "STANDARD": {"leader_move_bps": 3.0, "laggard_gap_bps": 2.0, "min_venues": 2},
            "STRICT": {"leader_move_bps": 5.0, "laggard_gap_bps": 3.0, "min_venues": 3},
        }),
    )
    return tuple(
        ForwardCandidate(family, variant, parameters, required)
        for family, required, variants in definitions
        for variant, parameters in variants.items()
    )


class ShadowExecutionEngine:
    """Conservative public-book simulation. This class exposes no order method."""

    @staticmethod
    def simulate_market(
        book: Mapping[str, Any], *, direction: str, notional_usd: float = 1_000,
        fee_bps: float = 5.0, latency_ms: int = 250,
        latency_penalty_bps_per_100ms: float = .15,
    ) -> dict[str, Any]:
        if direction not in {"LONG", "SHORT"}:
            raise ValueError("direction must be LONG or SHORT")
        if not book.get("valid") or not book.get("best_bid") or not book.get("best_ask"):
            return {"status": "NO_FILL", "reason": "BOOK_INVALID", "execution_authority": False}
        reference = float(book["mid"])
        observable = float(book["best_ask"] if direction == "LONG" else book["best_bid"])
        latency_penalty_bps = max(0, latency_ms) / 100 * latency_penalty_bps_per_100ms
        price = observable * (1 + latency_penalty_bps / 10_000 if direction == "LONG" else 1 - latency_penalty_bps / 10_000)
        spread_drag_bps = abs(price / reference - 1) * 10_000
        return {
            "status": "SIMULATED_FILL", "mode": "SHADOW", "direction": direction,
            "notional_usd": notional_usd, "reference_mid": reference,
            "simulated_fill_price": price, "observable_touch_price": observable,
            "spread_and_latency_drag_bps": spread_drag_bps,
            "fee_bps_one_way": fee_bps, "latency_ms": latency_ms,
            "execution_authority": False, "actual_order_id": None,
        }


class ForwardShadowEngine:
    def __init__(self, store: AppendOnlyEventStore):
        self.store = store
        self.candidates = frozen_forward_candidates()

    @staticmethod
    def _direction(spec: ForwardCandidate, snapshot: Mapping[str, Any], cross: Mapping[str, Any]) -> str | None:
        params = spec.parameters
        flow5 = snapshot["trade_flow"]["horizons_ms"]["5000"]
        flow30 = snapshot["trade_flow"]["horizons_ms"]["30000"]
        book = snapshot["book"]
        imbalance = float(book.get("depth_imbalance") or 0)
        if spec.family == "M1_FLOW_BOOK_MOMENTUM":
            if float(book.get("spread_bps") or math.inf) > params["max_spread_bps"]:
                return None
            if (flow5["normalized_delta"] or 0) >= params["delta_5s"] and imbalance >= params["book_imbalance"]:
                return "LONG"
            if (flow5["normalized_delta"] or 0) <= -params["delta_5s"] and imbalance <= -params["book_imbalance"]:
                return "SHORT"
        elif spec.family == "M2_ABSORPTION_REVERSAL":
            progress = snapshot.get("price_progress_5m_bps")
            if progress is None:
                return None
            if (flow30["normalized_delta"] or 0) >= params["delta_30s"] and progress <= params["max_progress_bps"] and imbalance <= -params["opposite_depth_imbalance"]:
                return "SHORT"
            if (flow30["normalized_delta"] or 0) <= -params["delta_30s"] and progress >= -params["max_progress_bps"] and imbalance >= params["opposite_depth_imbalance"]:
                return "LONG"
        elif spec.family == "M3_LIQUIDATION_EXHAUSTION":
            liquidations = snapshot["liquidations"]["60000"]
            flow = flow5["normalized_delta"]
            oi = snapshot["derivatives"].get("open_interest")
            oi_change = snapshot["derivatives"].get("open_interest_change")
            if oi is None or oi_change is None or oi_change >= params["oi_drop"] or flow is None or abs(flow) > params["flow_exhaustion_abs"]:
                return None
            if liquidations["forced_sell_notional"] >= params["liquidation_60s_usd"]:
                return "LONG"
            if liquidations["forced_buy_notional"] >= params["liquidation_60s_usd"]:
                return "SHORT"
        elif spec.family == "M4_LIQUIDITY_VACUUM_BREAKOUT":
            if float(book.get("spread_bps") or math.inf) > params["max_spread_bps"]:
                return None
            delta = flow5["normalized_delta"] or 0
            if book.get("ask_liquidity_within_bps", math.inf) <= params["near_depth_usd"] and delta >= params["delta_5s"]:
                return "LONG"
            if book.get("bid_liquidity_within_bps", math.inf) <= params["near_depth_usd"] and delta <= -params["delta_5s"]:
                return "SHORT"
        elif spec.family == "M5_CROSS_VENUE_LEAD_LAG" and cross.get("status") == "VALID":
            if int(cross["venue_count"]) < params["min_venues"] or float(cross["dispersion_bps"]) < params["laggard_gap_bps"]:
                return None
            leader = cross.get("leading_move_venue")
            if abs(float(cross.get("mid_move_5s_bps", {}).get(leader, 0))) < params["leader_move_bps"]:
                return None
            venue = snapshot["venue"]
            deviation = float(cross["mid_deviation_bps"].get(venue, 0))
            if deviation <= -params["laggard_gap_bps"]:
                return "LONG"
            if deviation >= params["laggard_gap_bps"]:
                return "SHORT"
        return None

    def evaluate(self, snapshot: dict[str, Any], cross: dict[str, Any]) -> list[dict[str, Any]]:
        if snapshot["data_quality"]["status"] != "VALID" or snapshot.get("execution_authority"):
            return []
        snapshot_id = self.store.append_feature(snapshot | {"cross_venue": cross})
        decisions = []
        for spec in self.candidates:
            direction = self._direction(spec, snapshot, cross)
            if direction is None:
                continue
            evidence_ts = int(snapshot["receive_ts_ms"])
            identity = {
                "candidate_id": spec.candidate_id, "feature_snapshot_id": snapshot_id,
                "decision_ts_ms": evidence_ts, "venue": snapshot["venue"],
                "symbol": snapshot["symbol"], "direction": direction,
            }
            decision_id = hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
            simulations = {
                name: ShadowExecutionEngine.simulate_market(
                    snapshot["book"], direction=direction, latency_ms=latency,
                ) for name, latency in LATENCY_SCENARIOS_MS.items()
            }
            decision = {
                **identity, "decision_id": decision_id, "family": spec.family,
                "variant": spec.variant, "candidate_version": spec.version,
                "first_evidence_ts_ms": evidence_ts, "feature_schema": snapshot["schema_version"],
                "mode": "SHADOW", "execution_authority": False,
                "latency_scenarios": simulations, "result": "FORWARD_EVIDENCE_PENDING",
            }
            self.store.append_shadow_decision(decision)
            decisions.append(decision)
        return decisions


class ForwardOutcomeLabeler:
    """Keeps future labels separate from feature snapshots and candidate logic."""

    def __init__(self, store: AppendOnlyEventStore):
        self.store = store
        self.pending: dict[str, dict[str, Any]] = {}
        restart_ts_ms = time.time_ns() // 1_000_000
        for unresolved in store.unresolved_shadow_decisions(LABEL_HORIZONS_MS):
            decision = unresolved["decision"]
            for horizon in unresolved["missing_horizons"]:
                store.append_label({
                    "decision_id": decision["decision_id"], "horizon_ms": horizon,
                    "observed_ts_ms": restart_ts_ms, "status": "INVALID",
                    "reason": "PROCESS_RESTART_GAP", "signed_return": None,
                    "mfe": None, "mae": None, "shadow_execution_outcomes": {},
                    "label_only": True, "feature_eligible": False,
                })

    def register(self, decision: Mapping[str, Any], entry_mid: float) -> None:
        self.pending[str(decision["decision_id"])] = {
            "decision": dict(decision), "entry_mid": float(entry_mid),
            "remaining": set(LABEL_HORIZONS_MS), "path": [],
        }

    def observe(self, *, venue: str, symbol: str, observed_ts_ms: int, mid: float,
                book: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        emitted = []
        for decision_id, state in list(self.pending.items()):
            decision = state["decision"]
            if decision["venue"] != venue or decision["symbol"] != symbol:
                continue
            direction_sign = 1 if decision["direction"] == "LONG" else -1
            signed_return = direction_sign * (float(mid) / state["entry_mid"] - 1)
            state["path"].append(signed_return)
            elapsed = observed_ts_ms - int(decision["decision_ts_ms"])
            for horizon in sorted(tuple(state["remaining"])):
                if elapsed < horizon:
                    continue
                shadow_outcomes: dict[str, Any] = {}
                if book and book.get("valid"):
                    close_direction = "SHORT" if decision["direction"] == "LONG" else "LONG"
                    sign = 1 if decision["direction"] == "LONG" else -1
                    for scenario, entry in decision["latency_scenarios"].items():
                        exit_fill = ShadowExecutionEngine.simulate_market(
                            book, direction=close_direction,
                            notional_usd=float(entry["notional_usd"]),
                            fee_bps=float(entry["fee_bps_one_way"]),
                            latency_ms=int(entry["latency_ms"]),
                        )
                        if entry["status"] != "SIMULATED_FILL" or exit_fill["status"] != "SIMULATED_FILL":
                            shadow_outcomes[scenario] = {"status": "NO_EXIT_FILL"}
                            continue
                        gross = sign * (
                            float(exit_fill["simulated_fill_price"]) /
                            float(entry["simulated_fill_price"]) - 1
                        )
                        fee_return = 2 * float(entry["fee_bps_one_way"]) / 10_000
                        net = gross - fee_return
                        shadow_outcomes[scenario] = {
                            "status": "CLOSED", "entry_fill": entry["simulated_fill_price"],
                            "exit_fill": exit_fill["simulated_fill_price"],
                            "gross_return": gross, "fee_return": fee_return,
                            "net_return": net, "net_pnl_usd": float(entry["notional_usd"]) * net,
                            "latency_ms": entry["latency_ms"], "execution_authority": False,
                        }
                label = {
                    "decision_id": decision_id, "horizon_ms": horizon,
                    "observed_ts_ms": observed_ts_ms, "entry_mid": state["entry_mid"],
                    "observed_mid": float(mid), "signed_return": signed_return,
                    "mfe": max(state["path"]), "mae": min(state["path"]),
                    "shadow_execution_outcomes": shadow_outcomes,
                    "label_only": True, "feature_eligible": False,
                }
                self.store.append_label(label)
                state["remaining"].remove(horizon)
                emitted.append(label)
            if not state["remaining"]:
                del self.pending[decision_id]
        return emitted


def registry_payload(created_at_utc: str) -> dict[str, Any]:
    return {
        "schema": "forward-shadow-experiment-registry-v1",
        "program_id": FORWARD_PROGRAM_ID,
        "created_at_utc": created_at_utc,
        "mode": "SHADOW_ONLY", "execution_authority": False,
        "evaluation_contract": {
            "fee_bps_one_way": 5.0, "label_horizons_ms": list(LABEL_HORIZONS_MS),
            "primary_horizon_ms": 300_000,
            "shadow_exit": "OPPOSITE_OBSERVABLE_TOUCH_PLUS_ADVERSE_LATENCY",
        },
        "candidate_count": 10,
        "candidates": [{
            **asdict(candidate), "candidate_id": candidate.candidate_id,
            "creation_timestamp": created_at_utc,
            "first_evidence_timestamp": None, "last_evidence_timestamp": None,
            "decisions": 0, "fills": 0, "pnl": None, "expectancy": None,
            "profit_factor": None, "drawdown": None, "mfe": None, "mae": None,
            "cost_assumptions": {"fee_bps_one_way": 5.0, "latency_scenarios_ms": LATENCY_SCENARIOS_MS},
            "result": "FORWARD_EVIDENCE_PENDING",
        } for candidate in frozen_forward_candidates()],
        "promotion_requirements": {
            "minimum_calendar_days": 30, "minimum_decisions": 500,
            "minimum_independent_clusters": 250, "minimum_volatility_states": 3,
            "minimum_directional_states": 3, "net_expectancy_r": .04,
            "profit_factor": 1.20, "maximum_drawdown_r": 25,
            "high_cost_positive": True, "stress_cost_positive": True,
            "normal_latency_positive": True, "stress_latency_positive": True,
            "minimum_positive_venue_count": 2,
        },
        "outcomes_inspected": False, "production_defaults_changed": False,
    }


def write_registry(path: str | Path, *, created_at_utc: str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(registry_payload(created_at_utc), indent=2, sort_keys=True) + "\n"
    target.write_text(payload, encoding="utf-8")
    return target
