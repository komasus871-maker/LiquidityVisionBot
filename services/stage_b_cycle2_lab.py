"""Research-only second and final Stage B challenger-generation cycle."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from typing import Any, Mapping

import pandas as pd

from services.baseline_edge_census import _ResearchMemory
from services.decision_quality import DecisionQualityEngine
from services.research_features import ATR, EMA50, EMA200
from services.research_replay import EntryPolicy, HistoricalReplayEngine, PerformanceAttribution, ReplayConfig, ReplayOutcome
from services.stage_b_alpha_lab import (
    BASE_COST, DEVELOPMENT_ACCESS_END, DEVELOPMENT_END, DEVELOPMENT_START,
    HIGHER_COST, PREREGISTRATION_HASH as MATERIALIZATION_PREREGISTRATION_HASH,
    ROOT as DATA_ROOT, SCHEMA as CYCLE1_SCHEMA, STRESS_COST, _authorized_plan,
    _bootstrap, _cluster_report, _code_identity, _features, _fold_metrics, _gate,
    _stable_hash, _symbol_report, load_development_frames,
)


SCHEMA = "stage-b-alpha-challenger-cycle2-v1"
FEATURE_VERSION = "stage-b-cycle2-causal-breakout-momentum-v1"
ROOT = Path("research_artifacts/stage_b_cycle2")

FAMILY_CONFIGS = (
    ("D1_RANGE_BREAKOUT_LONG", "BASE", "LONG", {"lookback": 48, "ema_alignment": True, "return24_sign": 1, "expansion": 1.10, "volume_ratio": 1.00}),
    ("D1_RANGE_BREAKOUT_LONG", "STRICT", "LONG", {"lookback": 72, "separation_atr": .75, "return24_sign": 1, "expansion": 1.30, "volume_ratio": 1.20}),
    ("D1_RANGE_BREAKOUT_LONG", "BROAD", "LONG", {"lookback": 24, "slope_sign": 1, "return6_sign": 1, "expansion": .90, "volume_ratio": .80}),
    ("D2_RANGE_BREAKOUT_SHORT", "BASE", "SHORT", {"lookback": 48, "ema_alignment": True, "return24_sign": -1, "expansion": 1.10, "volume_ratio": 1.00}),
    ("D2_RANGE_BREAKOUT_SHORT", "STRICT", "SHORT", {"lookback": 72, "separation_atr": .85, "return24_sign": -1, "expansion": 1.40, "volume_ratio": 1.30}),
    ("D2_RANGE_BREAKOUT_SHORT", "BROAD", "SHORT", {"lookback": 24, "slope_sign": -1, "return6_sign": -1, "expansion": .95, "volume_ratio": .90}),
    ("D3_MOMENTUM_EXPANSION_LONG", "BASE", "LONG", {"move_atr": 1.0, "return24_sign": 1, "ema50_side": 1, "efficiency": .35, "volume_ratio": .90}),
    ("D3_MOMENTUM_EXPANSION_LONG", "STRICT", "LONG", {"move_atr": 1.5, "separation_atr": .50, "efficiency": .45, "volume_ratio": 1.10}),
    ("D3_MOMENTUM_EXPANSION_LONG", "BROAD", "LONG", {"move_atr": .7, "slope_sign": 1, "efficiency": .28, "volume_ratio": .75}),
    ("D4_MOMENTUM_EXPANSION_SHORT", "BASE", "SHORT", {"move_atr": -1.1, "return24_sign": -1, "ema50_side": -1, "efficiency": .38, "volume_ratio": 1.00}),
    ("D4_MOMENTUM_EXPANSION_SHORT", "STRICT", "SHORT", {"move_atr": -1.7, "separation_atr": .60, "efficiency": .48, "volume_ratio": 1.20}),
    ("D4_MOMENTUM_EXPANSION_SHORT", "BROAD", "SHORT", {"move_atr": -.8, "slope_sign": -1, "efficiency": .32, "volume_ratio": .85}),
)


def preregistration_payload() -> dict[str, Any]:
    return {
        "schema": "stage-b-cycle2-preregistration-v1",
        "parent_materialization_preregistration_hash": MATERIALIZATION_PREREGISTRATION_HASH,
        "development": {"start": DEVELOPMENT_START.isoformat(), "end": DEVELOPMENT_END.isoformat(),
                        "access_end": DEVELOPMENT_ACCESS_END.isoformat()},
        "families": [{"family": family, "variant": variant, "direction": direction, "parameters": parameters}
                     for family, variant, direction, parameters in FAMILY_CONFIGS],
        "variant_count": 12, "entry": "NEXT_OPEN_MARKET", "max_holding_bars": 120,
        "costs": {"base": asdict(BASE_COST), "higher": asdict(HIGHER_COST), "stress": asdict(STRESS_COST)},
        "promotion_contract": "UNCHANGED_FROM_STAGE_B_CYCLE1",
    }


CYCLE2_PREREGISTRATION_HASH = _stable_hash(preregistration_payload())


@dataclass(frozen=True)
class CandidateSpec:
    family: str
    variant: str
    direction: str
    parameters: Mapping[str, Any]

    @property
    def candidate_id(self) -> str:
        return f"{self.family}:{self.variant}"

    @property
    def config_hash(self) -> str:
        return _stable_hash({"schema": SCHEMA, "preregistration_hash": CYCLE2_PREREGISTRATION_HASH,
                             "family": self.family, "variant": self.variant, "direction": self.direction,
                             "parameters": dict(self.parameters), "entry": "NEXT_OPEN_MARKET",
                             "risk_atr": 1.5, "risk_floor_pct": .0035, "target_atr": 2.0,
                             "target_r_floor": 1.2, "max_holding_bars": 120})


def frozen_candidates() -> tuple[CandidateSpec, ...]:
    return tuple(CandidateSpec(family, variant, direction, parameters)
                 for family, variant, direction, parameters in FAMILY_CONFIGS)


def _enhanced_features(frame: pd.DataFrame) -> pd.DataFrame:
    frame = _features(frame)
    high, low, close = frame["high"].astype(float), frame["low"].astype(float), frame["close"].astype(float)
    previous_close = close.shift(1)
    true_range = pd.concat((high - low, (high - previous_close).abs(), (low - previous_close).abs()), axis=1).max(axis=1)
    frame["__sb2_expansion"] = true_range / true_range.shift(1).rolling(20).mean().replace(0, float("nan"))
    frame["__sb2_move_6h_atr"] = [
        0.0 if index < 6 or not float(row[ATR]["atr"]) else
        (float(row["close"]) - float(frame.iloc[index - 6]["close"])) / float(row[ATR]["atr"])
        for index, (_, row) in enumerate(frame.iterrows())
    ]
    for lookback in (24, 48, 72):
        frame[f"__sb2_prior_high_{lookback}"] = high.shift(1).rolling(lookback).max()
        frame[f"__sb2_prior_low_{lookback}"] = low.shift(1).rolling(lookback).min()
    return frame


def family_signal(spec: CandidateSpec, row: Mapping[str, Any]) -> bool:
    p, close = spec.parameters, float(row["close"])
    tests: list[bool]
    if spec.family.startswith("D1_") or spec.family.startswith("D2_"):
        lookback = int(p["lookback"])
        breakout = close > row[f"__sb2_prior_high_{lookback}"] if spec.direction == "LONG" else close < row[f"__sb2_prior_low_{lookback}"]
        tests = [breakout, row["__sb2_expansion"] >= p["expansion"], row["__sb_volume_ratio"] >= p["volume_ratio"]]
        if p.get("ema_alignment"):
            tests.append(float(row[EMA50]) > float(row[EMA200]) if spec.direction == "LONG" else float(row[EMA50]) < float(row[EMA200]))
        if "separation_atr" in p:
            tests.append(row["__sb_separation_atr"] >= p["separation_atr"])
        if p.get("return24_sign"):
            tests.append(row["__sb_return_24h"] * p["return24_sign"] > 0)
        if p.get("return6_sign"):
            tests.append(row["__sb_return_6h"] * p["return6_sign"] > 0)
        if p.get("slope_sign"):
            tests.append(row["__sb_ema50_slope_atr"] * p["slope_sign"] > 0)
    else:
        threshold = float(p["move_atr"])
        tests = [row["__sb2_move_6h_atr"] >= threshold if spec.direction == "LONG" else row["__sb2_move_6h_atr"] <= threshold,
                 row["__sb_efficiency_24h"] >= p["efficiency"], row["__sb_volume_ratio"] >= p["volume_ratio"]]
        if p.get("return24_sign"):
            tests.append(row["__sb_return_24h"] * p["return24_sign"] > 0)
        if p.get("ema50_side"):
            tests.append((close - float(row[EMA50])) * p["ema50_side"] > 0)
        if "separation_atr" in p:
            tests.append(row["__sb_separation_atr"] >= p["separation_atr"])
        if p.get("slope_sign"):
            tests.append(row["__sb_ema50_slope_atr"] * p["slope_sign"] > 0)
    return all(bool(value) for value in tests)


class StageBCycle2Lab:
    def __init__(self, materialization: Mapping[str, Any]):
        if materialization.get("preregistration_hash") != MATERIALIZATION_PREREGISTRATION_HASH:
            raise ValueError("Stage B materialization identity mismatch")
        self.materialization = materialization
        self.frames = {symbol: _enhanced_features(frame)
                       for symbol, frame in load_development_frames(materialization, DATA_ROOT).items()}

    @staticmethod
    def _plans(spec: CandidateSpec, symbol: str, frame: pd.DataFrame) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
        quality = DecisionQualityEngine()
        quality.memory = _ResearchMemory()
        plans: dict[str, dict[str, Any]] = {}
        counts = {"eligible_decisions": 0, "family_signals": 0, "decision_quality_approved": 0,
                  "decision_quality_rejected": 0}
        for _, row in frame.iterrows():
            decision_at = pd.Timestamp(row["time"]) + pd.Timedelta(hours=1)
            if not DEVELOPMENT_START <= decision_at <= DEVELOPMENT_END:
                continue
            counts["eligible_decisions"] += 1
            if not family_signal(spec, row):
                continue
            counts["family_signals"] += 1
            plan, _ = _authorized_plan(spec, symbol, row, quality)
            if plan is None:
                counts["decision_quality_rejected"] += 1
            else:
                counts["decision_quality_approved"] += 1
                plans[decision_at.isoformat()] = plan
        return plans, counts

    def run_development(self) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        for spec in frozen_candidates():
            plans_by_symbol = {}
            counts = {"eligible_decisions": 0, "family_signals": 0, "decision_quality_approved": 0,
                      "decision_quality_rejected": 0}
            for symbol, frame in self.frames.items():
                plans, local = self._plans(spec, symbol, frame)
                plans_by_symbol[symbol] = plans
                for key in counts:
                    counts[key] += local[key]
            scenarios: dict[str, list[ReplayOutcome]] = {}
            for label, costs in (("BASE_COST", BASE_COST), ("HIGHER_COST", HIGHER_COST), ("STRESS_COST", STRESS_COST)):
                outcomes = []
                for symbol, frame in self.frames.items():
                    replay = HistoricalReplayEngine(ReplayConfig("1h", entry_policy=EntryPolicy.MARKET_NEXT_OPEN,
                                                                  max_holding_bars=120, costs=costs))
                    outcomes.extend(replay.run_plans(frame, plans_by_symbol[symbol], metadata={
                        "data_provider": "OKX_PUBLIC_SWAP", "decision_authority": DecisionQualityEngine.AUTHORITY,
                        "decision_version": DecisionQualityEngine.DECISION_VERSION,
                        "decision_source": "STAGE_B_ALPHA_CHALLENGER_CYCLE2", "variants_evaluated": 12,
                        "sample_role": "DEVELOPMENT", "split_id": "STAGE_B_CYCLE2_DEVELOPMENT",
                        "evaluation_window": f"{DEVELOPMENT_START.isoformat()}/{DEVELOPMENT_END.isoformat()}",
                    }))
                scenarios[label] = sorted(outcomes, key=lambda item: (item.decision_at, item.symbol))
            base, metrics = scenarios["BASE_COST"], PerformanceAttribution.metrics(scenarios["BASE_COST"])
            results.append({
                "experiment_id": _stable_hash({"candidate": spec.config_hash,
                                               "datasets": sorted(item["dataset_id"] for item in self.materialization["datasets"].values())}),
                "candidate": {"id": spec.candidate_id, "config_hash": spec.config_hash, "family": spec.family,
                              "variant": spec.variant, "direction": spec.direction, "parameters": dict(spec.parameters),
                              "parent_hash": CYCLE2_PREREGISTRATION_HASH},
                "counts": counts, "metrics": metrics, "folds": _fold_metrics(base),
                "uncertainty": _bootstrap(base, int(spec.config_hash[:8], 16)),
                "mfe_mae": {"average_mfe_r": metrics["average_mfe_r"], "average_mae_r": metrics["average_mae_r"]},
                "clusters": _cluster_report(base), "symbols": _symbol_report(base),
                "cost_stress": {label: PerformanceAttribution.metrics(items) for label, items in scenarios.items()},
                "integrity": {"causal": True, "decision_quality": True, "canonical_replay": True,
                              "trade_plan_integrity": True, "post_development_rows_loaded": False,
                              "protected_existing_partitions_accessed": []},
                "outcomes": {label: [item.as_dict() for item in items] for label, items in scenarios.items()},
            })
        for record in results:
            family = [item for item in results if item["candidate"]["family"] == record["candidate"]["family"]]
            passed, failures = _gate(record, family)
            record["development_gate"] = {"passed": passed, "failures": failures}
        qualifiers = [item for item in results if item["development_gate"]["passed"]]
        qualifiers.sort(key=lambda item: (-median(fold["metrics"]["expectancy_r"] for fold in item["folds"]),
                                          item["metrics"]["max_drawdown"], -item["clusters"]["cluster_count"]))
        selected = qualifiers[0]["candidate"] if qualifiers else None
        payload = {
            "schema": SCHEMA, "role": "DEVELOPMENT", "preregistration_hash": CYCLE2_PREREGISTRATION_HASH,
            "parent_materialization_preregistration_hash": MATERIALIZATION_PREREGISTRATION_HASH,
            "feature_version": FEATURE_VERSION, "code_identity": _code_identity(),
            "datasets": self.materialization["datasets"],
            "split": {"start": DEVELOPMENT_START.isoformat(), "end": DEVELOPMENT_END.isoformat(),
                      "access_end": DEVELOPMENT_ACCESS_END.isoformat()},
            "costs": {"BASE_COST": asdict(BASE_COST), "HIGHER_COST": asdict(HIGHER_COST), "STRESS_COST": asdict(STRESS_COST)},
            "candidates": results, "selected_validation_candidate": selected,
            "validation_accessed": False, "blind_accessed": False, "protected_existing_partitions_accessed": [],
            "status": "DEVELOPMENT_CANDIDATE_FROZEN" if selected else "NO_DEVELOPMENT_QUALIFIER",
        }
        payload["artifact_hash"] = _stable_hash(payload)
        return payload


def write_artifacts(payload: Mapping[str, Any]) -> tuple[Path, Path]:
    ROOT.mkdir(parents=True, exist_ok=True)
    prereg = preregistration_payload()
    prereg_path = ROOT / f"preregistration-{CYCLE2_PREREGISTRATION_HASH}.json"
    if not prereg_path.exists():
        prereg_path.write_text(json.dumps(prereg, indent=2, sort_keys=True), encoding="utf-8")
    artifact = ROOT / f"development-{payload['artifact_hash']}.json"
    artifact.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    registry = {"schema": SCHEMA, "preregistration_hash": CYCLE2_PREREGISTRATION_HASH,
                "development_artifact": artifact.name, "records": [
                    {key: value for key, value in record.items() if key != "outcomes"} for record in payload["candidates"]],
                "selected_validation_candidate": payload["selected_validation_candidate"],
                "validation_accessed": False, "blind_accessed": False,
                "protected_existing_partitions_accessed": []}
    registry["registry_hash"] = _stable_hash(registry)
    registry_path = ROOT / f"experiment-register-{registry['registry_hash']}.json"
    registry_path.write_text(json.dumps(registry, indent=2, sort_keys=True), encoding="utf-8")
    return artifact, registry_path
