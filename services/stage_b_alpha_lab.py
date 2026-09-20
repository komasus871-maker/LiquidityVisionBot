"""Research-only Stage B alpha challenger laboratory.

The module consumes only the preregistered Development interval and its
settlement tail.  It has no exchange, execution, scheduler, or production
strategy integration and cannot place orders.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Mapping

import pandas as pd

from services.baseline_edge_census import DatasetManifest, DatasetStore, _ResearchMemory
from services.decision_quality import DecisionQualityEngine
from services.research_features import (
    ATR, DISPLACEMENT, EMA50, EMA200, PREMIUM, REGIME, RSI, SWEEP,
    attach_causal_feature_cache,
)
from services.research_replay import (
    EntryPolicy, HistoricalReplayEngine, PerformanceAttribution, ReplayConfig,
    ReplayCostModel, ReplayOutcome,
)
from services.trade_plan_integrity import InvalidTradePlan, TradePlanIntegrity


SCHEMA = "stage-b-alpha-challenger-cycle1-v1"
FEATURE_VERSION = "stage-b-cycle1-causal-features-v1"
PREREGISTRATION_HASH = "442344d27cd71bab"
ROOT = Path("research_artifacts/stage_b_cycle1")
DEVELOPMENT_START = pd.Timestamp("2022-09-10T04:00:00Z")
DEVELOPMENT_END = pd.Timestamp("2023-09-01T23:00:00Z")
DEVELOPMENT_ACCESS_END = pd.Timestamp("2023-09-07T23:00:00Z")
BASE_COST = ReplayCostModel(.0005, .0003, source="STAGE_B_BASE_COST")
HIGHER_COST = ReplayCostModel(.00075, .0005, source="STAGE_B_HIGHER_COST")
STRESS_COST = ReplayCostModel(.0010, .0010, source="STAGE_B_STRESS_COST")


def _stable_hash(value: Any, length: int = 16) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(payload).hexdigest()[:length]


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
        return _stable_hash({
            "schema": SCHEMA, "preregistration_hash": PREREGISTRATION_HASH,
            "family": self.family, "variant": self.variant,
            "direction": self.direction, "parameters": dict(self.parameters),
            "entry": "NEXT_OPEN_MARKET", "risk_atr": 1.5,
            "risk_floor_pct": .0035, "target_atr": 2.0,
            "target_r_floor": 1.2, "max_holding_bars": 120,
        })


def frozen_candidates() -> tuple[CandidateSpec, ...]:
    """Return the exactly 12 preregistered configurations."""
    return (
        CandidateSpec("C1_TREND_PULLBACK_LONG", "BASE", "LONG",
                      {"efficiency": .32, "distance_atr": .75, "volume_ratio": .85}),
        CandidateSpec("C1_TREND_PULLBACK_LONG", "STRICT", "LONG",
                      {"separation_atr": .50, "efficiency": .40, "distance_atr": .50, "volume_ratio": 1.00}),
        CandidateSpec("C1_TREND_PULLBACK_LONG", "BROAD", "LONG",
                      {"positive_ema50_slope": True, "efficiency": .28, "distance_atr": 1.00, "volume_ratio": .70}),
        CandidateSpec("C2_TREND_PULLBACK_SHORT", "BASE", "SHORT",
                      {"efficiency": .36, "distance_atr": .65, "volume_ratio": .95}),
        CandidateSpec("C2_TREND_PULLBACK_SHORT", "STRICT", "SHORT",
                      {"separation_atr": .60, "efficiency": .44, "distance_atr": .45, "volume_ratio": 1.10}),
        CandidateSpec("C2_TREND_PULLBACK_SHORT", "BROAD", "SHORT",
                      {"negative_ema50_slope": True, "efficiency": .32, "distance_atr": .85, "volume_ratio": .80}),
        CandidateSpec("C3_LIQUIDITY_SWEEP_REVERSAL_LONG", "BASE", "LONG",
                      {"displacement": "BULLISH", "range_max": 50, "rsi_max": 55, "exclude_volatile_expansion": True}),
        CandidateSpec("C3_LIQUIDITY_SWEEP_REVERSAL_LONG", "STRICT", "LONG",
                      {"displacement": "MODERATE_OR_STRONG_BULLISH", "range_max": 38, "rsi_max": 48,
                       "volume_ratio": 1.00, "exclude_volatile_expansion": True}),
        CandidateSpec("C3_LIQUIDITY_SWEEP_REVERSAL_LONG", "BROAD", "LONG",
                      {"bullish_close": True, "range_max": 62, "rsi_max": 60, "volume_ratio": .70}),
        CandidateSpec("C4_LIQUIDITY_SWEEP_REVERSAL_SHORT", "BASE", "SHORT",
                      {"displacement": "BEARISH", "range_min": 55, "rsi_min": 50, "exclude_volatile_expansion": True}),
        CandidateSpec("C4_LIQUIDITY_SWEEP_REVERSAL_SHORT", "STRICT", "SHORT",
                      {"displacement": "MODERATE_OR_STRONG_BEARISH", "range_min": 65, "rsi_min": 58,
                       "volume_ratio": 1.10, "exclude_volatile_expansion": True}),
        CandidateSpec("C4_LIQUIDITY_SWEEP_REVERSAL_SHORT", "BROAD", "SHORT",
                      {"bearish_close": True, "range_min": 48, "rsi_min": 46, "volume_ratio": .80}),
    )


def _manifest(value: Mapping[str, Any]) -> DatasetManifest:
    return DatasetManifest(**dict(value))


def load_development_frames(materialization: Mapping[str, Any], root: Path = ROOT) -> dict[str, pd.DataFrame]:
    """Load no rows after the preregistered Development settlement tail."""
    frames: dict[str, pd.DataFrame] = {}
    requested_start = pd.Timestamp(materialization["preregistration"]["requested_start"])
    row_count = int((DEVELOPMENT_ACCESS_END - requested_start) / pd.Timedelta(hours=1)) + 1
    for symbol, raw_manifest in materialization["datasets"].items():
        manifest = _manifest(raw_manifest)
        path = root / f"{manifest.instrument}_{manifest.timeframe}_{manifest.dataset_id}.csv"
        frame = pd.read_csv(path, nrows=row_count)
        frame["time"] = pd.to_datetime(frame["time"], utc=True)
        if frame.empty or frame.iloc[-1]["time"] != DEVELOPMENT_ACCESS_END:
            raise ValueError(f"{symbol} Development settlement boundary mismatch")
        if (frame["time"] > DEVELOPMENT_ACCESS_END).any():
            raise ValueError("split guard admitted post-Development rows")
        expected = pd.date_range(requested_start, DEVELOPMENT_ACCESS_END, freq="h")
        if not pd.DatetimeIndex(frame["time"]).equals(expected):
            raise ValueError(f"{symbol} Development continuity contract failed")
        frames[symbol] = frame
    return frames


def _ratio(label: Any) -> float:
    match = re.search(r"\(([0-9.]+)x\)", str(label))
    return float(match.group(1)) if match else 1.0


def _features(frame: pd.DataFrame) -> pd.DataFrame:
    prepared = attach_causal_feature_cache(HistoricalReplayEngine(ReplayConfig("1h")).prepare(frame))
    close = prepared["close"].astype(float)
    opened = prepared["open"].astype(float)
    delta = close.diff().abs()
    path = delta.rolling(24).sum()
    prepared["__sb_return_6h"] = close.pct_change(6)
    prepared["__sb_return_24h"] = close.pct_change(24)
    prepared["__sb_efficiency_24h"] = (close.diff(24).abs() / path.replace(0, math.nan)).fillna(0.0)
    prepared["__sb_ema50_slope_atr"] = [
        0.0 if index < 10 or not float(row[ATR]["atr"]) else
        (float(row[EMA50]) - float(prepared.iloc[index - 10][EMA50])) / float(row[ATR]["atr"])
        for index, (_, row) in enumerate(prepared.iterrows())
    ]
    prepared["__sb_separation_atr"] = [
        abs(float(row[EMA50]) - float(row[EMA200])) / max(float(row[ATR]["atr"]), 1e-12)
        for _, row in prepared.iterrows()
    ]
    prepared["__sb_distance_atr"] = [
        abs(float(row["close"]) - float(row[EMA50])) / max(float(row[ATR]["atr"]), 1e-12)
        for _, row in prepared.iterrows()
    ]
    prepared["__sb_volume_ratio"] = prepared["__lv_volume"].map(_ratio)
    prepared["__sb_range_pct"] = prepared[PREMIUM].map(lambda value: float(value["premium"]))
    prepared["__sb_regime"] = prepared[REGIME].map(lambda value: str(value.get("code") or "UNKNOWN"))
    prepared["__sb_bullish_close"] = close > opened
    prepared["__sb_bearish_close"] = close < opened
    return prepared


def family_signal(spec: CandidateSpec, row: Mapping[str, Any]) -> tuple[bool, list[str]]:
    """Evaluate only frozen, causal family predicates."""
    p = spec.parameters
    close, ema50, ema200 = float(row["close"]), float(row[EMA50]), float(row[EMA200])
    common = []
    if spec.family == "C1_TREND_PULLBACK_LONG":
        common = [ema50 > ema200, row["__sb_return_6h"] > 0, row["__sb_return_24h"] > 0,
                  close > ema50, row["__sb_efficiency_24h"] >= p["efficiency"],
                  row["__sb_distance_atr"] <= p["distance_atr"], row["__sb_volume_ratio"] >= p["volume_ratio"]]
        if "separation_atr" in p:
            common.append(row["__sb_separation_atr"] >= p["separation_atr"])
        if p.get("positive_ema50_slope"):
            common.append(row["__sb_ema50_slope_atr"] > 0)
    elif spec.family == "C2_TREND_PULLBACK_SHORT":
        common = [ema50 < ema200, row["__sb_return_6h"] < 0, row["__sb_return_24h"] < 0,
                  close < ema50, row["__sb_efficiency_24h"] >= p["efficiency"],
                  row["__sb_distance_atr"] <= p["distance_atr"], row["__sb_volume_ratio"] >= p["volume_ratio"]]
        if "separation_atr" in p:
            common.append(row["__sb_separation_atr"] >= p["separation_atr"])
        if p.get("negative_ema50_slope"):
            common.append(row["__sb_ema50_slope_atr"] < 0)
    elif spec.family == "C3_LIQUIDITY_SWEEP_REVERSAL_LONG":
        displacement = str(row[DISPLACEMENT])
        common = ["Sell Side Stop Hunt" in str(row[SWEEP]), row["__sb_range_pct"] <= p["range_max"],
                  float(row[RSI]) <= p["rsi_max"], row["__sb_volume_ratio"] >= p.get("volume_ratio", 0.0)]
        if p.get("displacement") == "BULLISH":
            common.append("Bullish Displacement" in displacement)
        elif p.get("displacement") == "MODERATE_OR_STRONG_BULLISH":
            common.append("Bullish Displacement" in displacement and ("Moderate" in displacement or "Strong" in displacement))
        if p.get("bullish_close"):
            common.append(bool(row["__sb_bullish_close"]))
    else:
        displacement = str(row[DISPLACEMENT])
        common = ["Buy Side Stop Hunt" in str(row[SWEEP]), row["__sb_range_pct"] >= p["range_min"],
                  float(row[RSI]) >= p["rsi_min"], row["__sb_volume_ratio"] >= p.get("volume_ratio", 0.0)]
        if p.get("displacement") == "BEARISH":
            common.append("Bearish Displacement" in displacement)
        elif p.get("displacement") == "MODERATE_OR_STRONG_BEARISH":
            common.append("Bearish Displacement" in displacement and ("Moderate" in displacement or "Strong" in displacement))
        if p.get("bearish_close"):
            common.append(bool(row["__sb_bearish_close"]))
    if p.get("exclude_volatile_expansion"):
        common.append(row["__sb_regime"] != "VOLATILE_EXPANSION")
    return all(bool(value) for value in common), [f"predicate_{index}={bool(value)}" for index, value in enumerate(common)]


def _authorized_plan(spec: CandidateSpec, symbol: str, row: Mapping[str, Any], quality: DecisionQualityEngine) -> tuple[dict[str, Any] | None, str]:
    price = float(row["close"])
    atr = float(row[ATR]["atr"])
    risk = max(atr * 1.5, price * .0035)
    reward = max(atr * 2.0, risk * 1.2)
    if spec.direction == "LONG":
        stop, targets = price - risk, (price + reward, price + reward * 2, price + reward * 3)
    else:
        stop, first = price + risk, price - reward
        if first <= 0:
            return None, "TRADE_PLAN_TARGET_CROSSES_ZERO"
        second = max(price - reward * 2, first * .5)
        third = max(price - reward * 3, second * .5)
        targets = (first, second, third)
    try:
        TradePlanIntegrity.validate(spec.direction, price, stop, *targets)
    except InvalidTradePlan:
        return None, "TRADE_PLAN_INVALID"
    strength = 74.0 if spec.variant == "BASE" else 78.0 if spec.variant == "STRICT" else 70.0
    analysis = {
        "symbol": symbol, "timeframe": "1h", "timestamp": (pd.Timestamp(row["time"]) + pd.Timedelta(hours=1)).isoformat(),
        "direction": spec.direction, "direction_score": strength, "setup_score": strength,
        "score": strength, "confidence": strength, "probability": strength,
        "execution_status": "🟢 READY", "execution_readiness": strength,
        "entry_quality": strength, "risk_quality": 75.0, "blockers": 0,
        "price": price, "entry": price, "preferred_entry_low": price, "preferred_entry_high": price,
        "stop": stop, "tp1": targets[0], "tp2": targets[1], "tp3": targets[2],
        "rr": reward / risk, "atr": row[ATR], "plan_valid": True,
        "market_regime": row[REGIME], "regime": row[REGIME],
        "data_quality": {"status": "VALID"},
        "reasons": [f"✅ Preregistered {spec.family} {spec.variant} predicates passed"],
        "triggers": ["Closed-candle family conditions remain valid"],
        "score_components": [{"label": spec.family, "value": strength - 50.0}],
    }
    decision = quality.enrich(analysis, source="STAGE_B_ALPHA_CHALLENGER")
    authorized, reason = DecisionQualityEngine.authorization(decision)
    if not authorized:
        return None, reason
    return {
        "signal_id": f"{spec.config_hash}:{symbol}:{decision['decision_timestamp']}",
        "strategy_version": spec.config_hash, "symbol": symbol, "direction": spec.direction,
        "entry": price, "stop": stop, "targets": targets,
        "entry_policy": EntryPolicy.MARKET_NEXT_OPEN.value,
        "metadata": {"decision_authority": DecisionQualityEngine.AUTHORITY,
                     "decision_version": DecisionQualityEngine.DECISION_VERSION,
                     "decision_source": "STAGE_B_ALPHA_CHALLENGER",
                     "market_regime": row["__sb_regime"], "confidence": strength,
                     "probability": strength, "rr": reward / risk,
                     "candidate_id": spec.candidate_id, "candidate_config_hash": spec.config_hash},
    }, "APPROVED"


def _fold_metrics(outcomes: list[ReplayOutcome]) -> list[dict[str, Any]]:
    duration = (DEVELOPMENT_END - DEVELOPMENT_START + pd.Timedelta(hours=1)) / 4
    result = []
    for index in range(4):
        start = DEVELOPMENT_START + duration * index
        end = DEVELOPMENT_START + duration * (index + 1)
        items = [item for item in outcomes if start <= pd.Timestamp(item.decision_at) < end]
        result.append({"fold": index + 1, "start": start.isoformat(), "end_exclusive": end.isoformat(),
                       "metrics": PerformanceAttribution.metrics(items)})
    return result


def _bootstrap(outcomes: Iterable[ReplayOutcome], seed: int, resamples: int = 1000, block_size: int = 24) -> dict[str, Any]:
    values = [float(item.net_r) for item in outcomes if item.simulated_fill is not None]
    if not values:
        return {"sample_size": 0, "estimate": None, "lower": None, "upper": None,
                "seed": seed, "resamples": resamples, "block_size": block_size}
    rng, draws, count = random.Random(seed), [], len(values)
    for _ in range(resamples):
        sample = []
        while len(sample) < count:
            start = rng.randrange(count)
            sample.extend(values[(start + offset) % count] for offset in range(block_size))
        draws.append(sum(sample[:count]) / count)
    draws.sort()
    return {"sample_size": count, "estimate": sum(values) / count,
            "lower": draws[int((resamples - 1) * .025)], "upper": draws[int((resamples - 1) * .975)],
            "seed": seed, "resamples": resamples, "block_size": block_size}


def _cluster_report(outcomes: list[ReplayOutcome]) -> dict[str, Any]:
    groups: dict[str, list[ReplayOutcome]] = {}
    for item in outcomes:
        if item.simulated_fill is not None:
            groups.setdefault(f"{item.decision_at}|{item.direction}", []).append(item)
    nets = {key: sum(item.net_r for item in values) for key, values in groups.items()}
    positive_total = sum(value for value in nets.values() if value > 0)
    largest = max((value for value in nets.values() if value > 0), default=0.0)
    return {"cluster_count": len(groups), "correlated_cluster_count": sum(len(value) > 1 for value in groups.values()),
            "largest_positive_cluster_share": largest / positive_total if positive_total > 0 else None}


def _symbol_report(outcomes: list[ReplayOutcome]) -> dict[str, Any]:
    groups: dict[str, list[ReplayOutcome]] = {}
    for item in outcomes:
        if item.simulated_fill is not None:
            groups.setdefault(item.symbol, []).append(item)
    total = sum(len(items) for items in groups.values())
    positive_total = sum(max(0.0, sum(item.net_r for item in items)) for items in groups.values())
    metrics = {symbol: PerformanceAttribution.metrics(items) for symbol, items in sorted(groups.items())}
    fill_share = max((len(items) / total for items in groups.values()), default=0.0)
    positive_share = max((max(0.0, sum(item.net_r for item in items)) / positive_total for items in groups.values()), default=0.0) if positive_total else None
    return {"metrics": metrics, "largest_fill_share": fill_share, "largest_positive_net_r_share": positive_share}


def _code_identity() -> dict[str, Any]:
    try:
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        diff = subprocess.check_output(["git", "diff", "--binary"], text=False)
        return {"revision": revision, "dirty": bool(diff), "dirty_diff_sha256": hashlib.sha256(diff).hexdigest()}
    except (OSError, subprocess.CalledProcessError):
        return {"revision": "UNAVAILABLE", "dirty": None, "dirty_diff_sha256": None}


def _gate(record: Mapping[str, Any], family_records: list[Mapping[str, Any]]) -> tuple[bool, list[str]]:
    metrics, folds = record["metrics"], record["folds"]
    cluster, symbols, bootstrap = record["clusters"], record["symbols"], record["uncertainty"]
    checks = {
        "MIN_FILLS": metrics["trade_count"] >= 60,
        "MIN_CLUSTERS": cluster["cluster_count"] >= 40,
        "EXPECTANCY": metrics["expectancy_r"] >= .15,
        "PROFIT_FACTOR": metrics["profit_factor"] is not None and metrics["profit_factor"] >= 1.25,
        "THREE_POSITIVE_FOLDS": sum(item["metrics"]["expectancy_r"] > 0 for item in folds) >= 3,
        "NO_CATASTROPHIC_FOLD": all(item["metrics"]["trade_count"] < 10 or
            (item["metrics"]["expectancy_r"] > -.15 and (item["metrics"]["profit_factor"] or 0) > .75) for item in folds),
        "HIGHER_COST_POSITIVE": record["cost_stress"]["HIGHER_COST"]["expectancy_r"] > 0,
        "STRESS_FLOOR": record["cost_stress"]["STRESS_COST"]["expectancy_r"] >= -.05,
        "SYMBOL_FILL_DIVERSIFICATION": symbols["largest_fill_share"] <= .60,
        "SYMBOL_RETURN_DIVERSIFICATION": symbols["largest_positive_net_r_share"] is not None and symbols["largest_positive_net_r_share"] <= .60,
        "CLUSTER_DOMINANCE": cluster["largest_positive_cluster_share"] is not None and cluster["largest_positive_cluster_share"] <= .15,
        "NEIGHBOR_STABILITY": len(family_records) == 3 and all(
            other["metrics"]["trade_count"] >= 30 and other["metrics"]["expectancy_r"] > 0 and
            other["metrics"]["profit_factor"] is not None and other["metrics"]["profit_factor"] > 1.0
            for other in family_records if other["candidate"]["id"] != record["candidate"]["id"]),
        "BOOTSTRAP_FLOOR": bootstrap["lower"] is not None and bootstrap["lower"] > -.05,
        "CAUSAL_DQE_REPLAY": record["integrity"]["causal"] and record["integrity"]["decision_quality"] and record["integrity"]["canonical_replay"],
    }
    return all(checks.values()), [key for key, passed in checks.items() if not passed]


class StageBAlphaLab:
    def __init__(self, materialization: Mapping[str, Any]):
        if materialization.get("preregistration_hash") != PREREGISTRATION_HASH:
            raise ValueError("Stage B preregistration hash mismatch")
        if materialization.get("protected_existing_partitions_accessed"):
            raise ValueError("protected partition access is not permitted")
        self.materialization = materialization
        self.frames = {symbol: _features(frame) for symbol, frame in load_development_frames(materialization).items()}

    @staticmethod
    def _plans(spec: CandidateSpec, symbol: str, frame: pd.DataFrame) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
        quality = DecisionQualityEngine()
        quality.memory = _ResearchMemory()
        plans: dict[str, dict[str, Any]] = {}
        counters = {"eligible_decisions": 0, "family_signals": 0, "decision_quality_approved": 0,
                    "decision_quality_rejected": 0}
        for _, row in frame.iterrows():
            decision_at = pd.Timestamp(row["time"]) + pd.Timedelta(hours=1)
            if not DEVELOPMENT_START <= decision_at <= DEVELOPMENT_END:
                continue
            counters["eligible_decisions"] += 1
            matched, _ = family_signal(spec, row)
            if not matched:
                continue
            counters["family_signals"] += 1
            plan, _ = _authorized_plan(spec, symbol, row, quality)
            if plan is None:
                counters["decision_quality_rejected"] += 1
                continue
            counters["decision_quality_approved"] += 1
            plans[decision_at.isoformat()] = plan
        return plans, counters

    def run_development(self) -> dict[str, Any]:
        candidates = frozen_candidates()
        results: list[dict[str, Any]] = []
        plans_by_candidate: dict[str, dict[str, dict[str, Any]]] = {}
        for spec in candidates:
            plans_by_symbol, counters = {}, {"eligible_decisions": 0, "family_signals": 0,
                                             "decision_quality_approved": 0, "decision_quality_rejected": 0}
            for symbol, frame in self.frames.items():
                plans, local = self._plans(spec, symbol, frame)
                plans_by_symbol[symbol] = plans
                for key in counters:
                    counters[key] += local[key]
            plans_by_candidate[spec.candidate_id] = plans_by_symbol
            scenario_outcomes: dict[str, list[ReplayOutcome]] = {}
            for label, costs in (("BASE_COST", BASE_COST), ("HIGHER_COST", HIGHER_COST), ("STRESS_COST", STRESS_COST)):
                combined: list[ReplayOutcome] = []
                for symbol, frame in self.frames.items():
                    engine = HistoricalReplayEngine(ReplayConfig("1h", entry_policy=EntryPolicy.MARKET_NEXT_OPEN,
                                                                   max_holding_bars=120, costs=costs))
                    combined.extend(engine.run_plans(frame, plans_by_symbol[symbol], metadata={
                        "data_provider": "OKX_PUBLIC_SWAP", "decision_authority": DecisionQualityEngine.AUTHORITY,
                        "decision_version": DecisionQualityEngine.DECISION_VERSION,
                        "decision_source": "STAGE_B_ALPHA_CHALLENGER", "variants_evaluated": 12,
                        "sample_role": "DEVELOPMENT", "split_id": "STAGE_B_CYCLE1_DEVELOPMENT",
                        "evaluation_window": f"{DEVELOPMENT_START.isoformat()}/{DEVELOPMENT_END.isoformat()}",
                    }))
                scenario_outcomes[label] = sorted(combined, key=lambda item: (item.decision_at, item.symbol))
            base = scenario_outcomes["BASE_COST"]
            record = {
                "experiment_id": _stable_hash({"candidate": spec.config_hash,
                                               "datasets": sorted(item["dataset_id"] for item in self.materialization["datasets"].values())}),
                "candidate": {"id": spec.candidate_id, "config_hash": spec.config_hash,
                              "family": spec.family, "variant": spec.variant, "direction": spec.direction,
                              "parameters": dict(spec.parameters), "parent_hash": PREREGISTRATION_HASH},
                "counts": counters, "metrics": PerformanceAttribution.metrics(base),
                "folds": _fold_metrics(base), "uncertainty": _bootstrap(base, int(spec.config_hash[:8], 16)),
                "mfe_mae": {"average_mfe_r": PerformanceAttribution.metrics(base)["average_mfe_r"],
                            "average_mae_r": PerformanceAttribution.metrics(base)["average_mae_r"]},
                "clusters": _cluster_report(base), "symbols": _symbol_report(base),
                "cost_stress": {label: PerformanceAttribution.metrics(items) for label, items in scenario_outcomes.items()},
                "integrity": {"causal": True, "decision_quality": True, "canonical_replay": True,
                              "trade_plan_integrity": True, "post_development_rows_loaded": False,
                              "protected_existing_partitions_accessed": []},
                "outcomes": {label: [item.as_dict() for item in items] for label, items in scenario_outcomes.items()},
            }
            results.append(record)
        for record in results:
            family_records = [item for item in results if item["candidate"]["family"] == record["candidate"]["family"]]
            passed, failures = _gate(record, family_records)
            record["development_gate"] = {"passed": passed, "failures": failures}
        qualifiers = [record for record in results if record["development_gate"]["passed"]]
        qualifiers.sort(key=lambda record: (
            -median(item["metrics"]["expectancy_r"] for item in record["folds"]),
            record["metrics"]["max_drawdown"], -record["clusters"]["cluster_count"],
        ))
        selected = qualifiers[0]["candidate"] if qualifiers else None
        payload = {
            "schema": SCHEMA, "role": "DEVELOPMENT", "preregistration_hash": PREREGISTRATION_HASH,
            "feature_version": FEATURE_VERSION, "code_identity": _code_identity(),
            "datasets": self.materialization["datasets"],
            "split": {"start": DEVELOPMENT_START.isoformat(), "end": DEVELOPMENT_END.isoformat(),
                      "access_end": DEVELOPMENT_ACCESS_END.isoformat()},
            "costs": {"BASE_COST": asdict(BASE_COST), "HIGHER_COST": asdict(HIGHER_COST), "STRESS_COST": asdict(STRESS_COST)},
            "candidates": results, "selected_validation_candidate": selected,
            "validation_accessed": False, "blind_accessed": False,
            "protected_existing_partitions_accessed": [],
            "status": "DEVELOPMENT_CANDIDATE_FROZEN" if selected else "NO_DEVELOPMENT_QUALIFIER",
        }
        payload["artifact_hash"] = _stable_hash(payload)
        return payload


def write_development_artifact(payload: Mapping[str, Any], root: Path = ROOT) -> Path:
    path = root / f"development-{payload['artifact_hash']}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    registry = {
        "schema": SCHEMA, "preregistration_hash": PREREGISTRATION_HASH,
        "development_artifact": path.name, "development_artifact_hash": payload["artifact_hash"],
        "records": [{key: value for key, value in record.items() if key != "outcomes"}
                    for record in payload["candidates"]],
        "selected_validation_candidate": payload["selected_validation_candidate"],
        "validation_accessed": False, "blind_accessed": False,
        "protected_existing_partitions_accessed": [],
    }
    registry["registry_hash"] = _stable_hash(registry)
    registry_path = root / f"experiment-register-{registry['registry_hash']}.json"
    registry_path.write_text(json.dumps(registry, indent=2, sort_keys=True), encoding="utf-8")
    return path
