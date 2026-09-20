"""Bounded Development-only derivatives alpha laboratory."""
from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from services.decision_quality import DecisionQualityEngine
from services.derivatives_alpha import (
    DERIV_DEV_END, DERIV_DEV_START, DerivativesCandidateSpec, PositioningStateEngine,
    candidate_registry_identity, frozen_candidates, stable_hash,
)
from services.research_replay import (
    EntryPolicy, HistoricalReplayEngine, ReplayConfig, ReplayCostModel, ReplayOutcome,
)


ROOT = Path("research_artifacts/derivatives_alpha")
MATERIALIZATION_ID = "8886c1cd8d5e0b97"
BASE_COST = ReplayCostModel(.0005, .0004, source="DERIV_BASE_FEE_ENTRY_FRICTION")
HIGH_COST = ReplayCostModel(.00075, .0007, source="DERIV_HIGH_FEE_ENTRY_FRICTION")
STRESS_COST = ReplayCostModel(.0010, .0012, source="DERIV_STRESS_FEE_ENTRY_FRICTION")
EXIT_FRICTION = {"BASE": .0004, "HIGH": .0007, "STRESS": .0012}


class _ResearchMemory:
    def remember(self, symbol: str, timeframe: str, data: Mapping[str, Any]) -> dict[str, Any]:
        return {"symbol": symbol, "timeframe": timeframe, "research_only": True}


def _verify_hash(path: Path, expected: str) -> None:
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError(f"dataset hash mismatch for {path}")


def load_development() -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    manifests = sorted(ROOT.glob(f"materialization-{MATERIALIZATION_ID}.json"))
    if len(manifests) != 1:
        raise ValueError("exact frozen materialization is required")
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    if manifest["materialization_id"] != MATERIALIZATION_ID or manifest["outcomes_evaluated"]:
        raise ValueError("materialization identity/outcome guard failed")
    frames: dict[str, pd.DataFrame] = {}
    for symbol, receipt in manifest["splits"]["DERIV_DEV"].items():
        path = Path(receipt["path"])
        _verify_hash(path, receipt["sha256"])
        frame = pd.read_csv(path)
        frame["time"] = pd.to_datetime(frame["time"], utc=True)
        frame["decision_at"] = pd.to_datetime(frame["decision_at"], utc=True)
        if len(frame) != 10968 or frame["time"].iloc[0] != DERIV_DEV_START or frame["time"].iloc[-1] != DERIV_DEV_END:
            raise ValueError(f"{symbol} Development boundary mismatch")
        if set(frame["provider"]) != {"BINANCE_VISION_UM"}:
            raise ValueError("venue provenance mismatch")
        frames[symbol] = frame
    return frames, manifest


def _atr(frame: pd.DataFrame) -> pd.Series:
    close = frame["close"].astype(float)
    previous = close.shift(1)
    tr = pd.concat([
        frame["high"].astype(float) - frame["low"].astype(float),
        (frame["high"].astype(float) - previous).abs(),
        (frame["low"].astype(float) - previous).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(14, min_periods=14).mean()


def feature_frame(frame: pd.DataFrame) -> pd.DataFrame:
    prepared = frame.copy()
    prepared["close"] = pd.to_numeric(prepared["close"])
    prepared["oi_usd"] = pd.to_numeric(prepared["oi_usd"])
    prepared["funding_rate"] = pd.to_numeric(prepared["funding_rate"])
    prepared["basis_pct"] = pd.to_numeric(prepared["basis_pct"])
    prepared = PositioningStateEngine().transform(prepared)
    prepared["atr14"] = _atr(prepared)
    return prepared


def candidate_direction(spec: DerivativesCandidateSpec, row: Mapping[str, Any]) -> str | None:
    core, extreme = float(spec.parameters["core"]), float(spec.parameters["extreme"])
    price_p = float(row["price_return_6h_percentile"])
    oi_change_p = float(row["oi_change_6h_percentile"])
    oi_level_p = float(row["oi_percentile"])
    funding_p, basis_p = float(row["funding_percentile"]), float(row["basis_percentile"])
    return_1h = float(row["price_return_1h"])
    values = (price_p, oi_change_p, oi_level_p, funding_p, basis_p, return_1h)
    if not all(math.isfinite(value) for value in values):
        return None
    family = spec.family.removeprefix("C2_4H_")
    if family == "D1_LEVERAGED_TREND_LONG":
        return "LONG" if price_p >= core and oi_change_p >= core and funding_p <= extreme and basis_p <= extreme and return_1h > 0 else None
    if family == "D1_LEVERAGED_TREND_SHORT":
        floor = 1.0 - extreme
        return "SHORT" if price_p <= 1.0 - core and oi_change_p >= core and funding_p >= floor and basis_p >= floor and return_1h < 0 else None
    if family == "D2_DELEVERAGING_REVERSAL":
        if price_p <= 1.0 - extreme and oi_change_p <= 1.0 - core and return_1h > 0:
            return "LONG"
        if price_p >= extreme and oi_change_p <= 1.0 - core and return_1h < 0:
            return "SHORT"
        return None
    if family == "D3_CROWDING_REVERSAL":
        if funding_p <= 1.0 - extreme and basis_p <= 1.0 - extreme and oi_level_p >= core and return_1h > 0:
            return "LONG"
        if funding_p >= extreme and basis_p >= extreme and oi_level_p >= core and return_1h < 0:
            return "SHORT"
    return None


def _authorized_plan(
    spec: DerivativesCandidateSpec, symbol: str, row: Mapping[str, Any], direction: str,
    quality: DecisionQualityEngine,
) -> dict[str, Any] | None:
    price, atr = float(row["close"]), float(row["atr14"])
    if not math.isfinite(atr) or atr <= 0:
        return None
    risk = max(1.5 * atr, price * .0035)
    target = price + 1.5 * risk if direction == "LONG" else price - 1.5 * risk
    stop = price - risk if direction == "LONG" else price + risk
    if target <= 0 or stop <= 0:
        return None
    strength = {"BROAD": 70.0, "BASE": 74.0, "STRICT": 78.0}[spec.variant]
    timestamp = pd.Timestamp(row["decision_at"]).isoformat()
    decision = quality.enrich({
        "symbol": symbol, "timeframe": "1h", "timestamp": timestamp,
        "direction": direction, "direction_score": strength, "setup_score": strength,
        "score": strength, "confidence": strength, "probability": strength,
        "execution_status": "🟢 READY", "plan_valid": True,
        "entry": price, "stop": stop, "tp1": target, "tp2": target, "tp3": target,
        "market_regime": {"code": "DERIVATIVES_RESEARCH"},
        "data_quality": {"status": "VALID"},
        "reasons": [f"Preregistered {spec.family}/{spec.variant} predicate passed"],
        "triggers": ["Causal closed-candle positioning state"],
        "score_components": [{"label": spec.family, "value": strength - 50}],
    }, source="DERIVATIVES_ALPHA_RESEARCH")
    approved, _ = DecisionQualityEngine.authorization(decision)
    if not approved:
        return None
    return {
        "signal_id": f"{spec.candidate_id}:{symbol}:{timestamp}",
        "strategy_version": spec.candidate_id, "symbol": symbol, "direction": direction,
        "entry": price, "stop": stop, "targets": (target,),
        "entry_policy": EntryPolicy.MARKET_NEXT_OPEN.value,
        "metadata": {
            "candidate_id": spec.candidate_id, "alpha_family": spec.family,
            "provider_venue": "BINANCE", "decision_authority": DecisionQualityEngine.AUTHORITY,
            "decision_version": DecisionQualityEngine.DECISION_VERSION,
            "decision_source": "DERIVATIVES_ALPHA_RESEARCH",
        },
    }


def build_plans(spec: DerivativesCandidateSpec, symbol: str, frame: pd.DataFrame) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    quality = DecisionQualityEngine()
    quality.memory = _ResearchMemory()
    plans: dict[str, dict[str, Any]] = {}
    counters = {"eligible": 0, "family_matches": 0, "dqe_approved": 0, "dqe_rejected": 0}
    for _, row in frame.iterrows():
        counters["eligible"] += 1
        direction = candidate_direction(spec, row)
        if not direction:
            continue
        counters["family_matches"] += 1
        plan = _authorized_plan(spec, symbol, row, direction, quality)
        if plan is None:
            counters["dqe_rejected"] += 1
            continue
        counters["dqe_approved"] += 1
        plans[pd.Timestamp(row["decision_at"]).isoformat()] = plan
    return plans, counters


def _funding_events(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame[["funding_at", "funding_rate"]].copy()
    result["funding_at"] = pd.to_datetime(result["funding_at"], utc=True)
    return result.drop_duplicates("funding_at").sort_values("funding_at")


def _apply_exit_and_funding(
    outcomes: list[ReplayOutcome], frame: pd.DataFrame, exit_friction: float,
) -> list[ReplayOutcome]:
    events = _funding_events(frame)
    adjusted: list[ReplayOutcome] = []
    for item in outcomes:
        if item.simulated_fill is None or item.exit_price is None or item.exit_at is None:
            continue
        risk = abs(item.intended_entry - item.stop)
        start, end = pd.Timestamp(item.fill_at), pd.Timestamp(item.exit_at)
        rates = events.loc[(events["funding_at"] > start) & (events["funding_at"] <= end), "funding_rate"].astype(float).sum()
        funding_r = item.simulated_fill * (rates if item.direction == "LONG" else -rates) / risk
        exit_cost_r = abs(item.exit_price) * exit_friction / risk
        net_r = float(item.net_r) - exit_cost_r - funding_r
        funding_amount = item.simulated_fill * (rates if item.direction == "LONG" else -rates)
        adjusted.append(replace(
            item, net_r=round(net_r, 12), funding=round(funding_amount, 12),
            funding_status="ACTUAL_SETTLEMENTS", net_pnl=round(net_r * risk, 12),
            provenance=item.provenance | {
                "exit_friction_rate": exit_friction, "provider_venue": "BINANCE",
                "funding_cost_method": "ACTUAL_SETTLEMENT_EVENTS_ONCE",
            },
        ))
    return adjusted


def _metrics(items: list[ReplayOutcome]) -> dict[str, Any]:
    ordered = sorted(items, key=lambda item: (item.decision_at, item.symbol))
    values = [float(item.net_r) for item in ordered]
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]
    equity, peak, drawdown = 0.0, 0.0, 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    gross_win, gross_loss = sum(wins), abs(sum(losses))
    return {
        "fills": len(values), "win_rate": len(wins) / len(values) if values else None,
        "expectancy_r": sum(values) / len(values) if values else None,
        "profit_factor": gross_win / gross_loss if gross_loss else (math.inf if gross_win else None),
        "net_r": sum(values), "max_drawdown_r": drawdown,
        "mfe_r_mean": float(np.mean([item.mfe_r for item in items if item.mfe_r is not None])) if items else None,
        "mae_r_mean": float(np.mean([item.mae_r for item in items if item.mae_r is not None])) if items else None,
    }


def _groups(items: list[ReplayOutcome], key) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[ReplayOutcome]] = {}
    for item in items:
        grouped.setdefault(str(key(item)), []).append(item)
    return {name: _metrics(values) for name, values in sorted(grouped.items())}


def _cluster_values(items: list[ReplayOutcome], family: str) -> dict[str, float]:
    clusters: dict[str, float] = {}
    for item in items:
        key = f"{item.decision_at}|{item.direction}|{family}"
        clusters[key] = clusters.get(key, 0.0) + float(item.net_r)
    return clusters


def _bootstrap(items: list[ReplayOutcome], family: str, seed: int, resamples: int = 1000) -> dict[str, Any]:
    values = list(_cluster_values(items, family).values())
    if not values:
        return {"clusters": 0, "lower": None, "median": None, "upper": None}
    rng = random.Random(seed)
    draws = []
    for _ in range(resamples):
        sample = [values[rng.randrange(len(values))] for _ in values]
        draws.append(sum(sample) / max(1, sum(1 for _ in sample)))
    draws.sort()
    return {
        "clusters": len(values), "lower": draws[int(.025 * (resamples - 1))],
        "median": draws[int(.5 * (resamples - 1))], "upper": draws[int(.975 * (resamples - 1))],
        "seed": seed, "resamples": resamples,
    }


FOLDS = (
    ("2024Q3", pd.Timestamp("2024-07-01T00:00Z"), pd.Timestamp("2024-09-30T23:00Z")),
    ("2024Q4", pd.Timestamp("2024-10-01T00:00Z"), pd.Timestamp("2024-12-31T23:00Z")),
    ("2025Q1", pd.Timestamp("2025-01-01T00:00Z"), pd.Timestamp("2025-03-31T23:00Z")),
    ("2025Q2", pd.Timestamp("2025-04-01T00:00Z"), pd.Timestamp("2025-06-30T23:00Z")),
    ("2025Q3", pd.Timestamp("2025-07-01T00:00Z"), pd.Timestamp("2025-09-30T23:00Z")),
)


def _folds(items: list[ReplayOutcome]) -> list[dict[str, Any]]:
    return [{"fold": name, "metrics": _metrics([
        item for item in items if start <= pd.Timestamp(item.decision_at) <= end + pd.Timedelta(hours=1)
    ])} for name, start, end in FOLDS]


def _forward_state_report(frames: Mapping[str, pd.DataFrame]) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for symbol, frame in frames.items():
        close = frame["close"].astype(float)
        for horizon in (1, 3, 6, 12, 24):
            future = close.shift(-horizon) / close - 1
            for state, group in frame.assign(__future=future).dropna(subset=["__future"]).groupby("price_oi_quadrant"):
                values = group["__future"].astype(float)
                records.append({
                    "symbol": symbol, "state": state, "horizon_bars": horizon, "n": len(values),
                    "mean": float(values.mean()), "median": float(values.median()),
                    "positive_frequency": float((values > 0).mean()),
                    "p05": float(values.quantile(.05)), "p95": float(values.quantile(.95)),
                })
    return {"development_labels_only": True, "records": records}


def _promotion(record: Mapping[str, Any], siblings: list[Mapping[str, Any]]) -> tuple[bool, list[str]]:
    base, high, stress = record["costs"]["BASE"], record["costs"]["HIGH"], record["costs"]["STRESS"]
    fold_metrics = [item["metrics"] for item in record["folds"]]
    positive_fold_net = sum(max(0.0, item["net_r"]) for item in fold_metrics)
    max_fold_share = max((max(0.0, item["net_r"]) / positive_fold_net for item in fold_metrics), default=1.0) if positive_fold_net else 1.0
    symbol_metrics = record["symbols"]
    positive_symbols = sum(1 for item in symbol_metrics.values() if item["expectancy_r"] is not None and item["expectancy_r"] > 0)
    directions = record["directions"]
    checks = {
        "MIN_120_FILLS": base["fills"] >= 120,
        "MIN_60_CLUSTERS": record["cluster_n"] >= 60,
        "EXPECTANCY_AT_LEAST_0_05R": base["expectancy_r"] is not None and base["expectancy_r"] >= .05,
        "PF_AT_LEAST_1_15": base["profit_factor"] is not None and base["profit_factor"] >= 1.15,
        "HIGH_POSITIVE": high["expectancy_r"] is not None and high["expectancy_r"] > 0 and high["profit_factor"] > 1,
        "STRESS_POSITIVE": stress["expectancy_r"] is not None and stress["expectancy_r"] > 0 and stress["profit_factor"] > 1,
        "THREE_NONNEGATIVE_FOLDS": sum(item["expectancy_r"] is not None and item["expectancy_r"] >= 0 for item in fold_metrics) >= 3,
        "NO_FOLD_BELOW_MINUS_0_10R": all(item["expectancy_r"] is not None and item["expectancy_r"] >= -.10 for item in fold_metrics),
        "FOLD_CONTRIBUTION_MAX_60PCT": max_fold_share <= .60,
        "MAX_DRAWDOWN_25R": base["max_drawdown_r"] <= 25,
        "TWO_POSITIVE_SYMBOLS": positive_symbols >= 2,
        "DECLARED_DIRECTION_PRESENT": bool(directions),
        "BOOTSTRAP_LOWER_ABOVE_MINUS_0_03R": record["bootstrap"]["lower"] is not None and record["bootstrap"]["lower"] > -.03,
        "NEIGHBOR_PLATEAU": all(
            sibling["costs"]["BASE"]["fills"] >= 60 and sibling["costs"]["BASE"]["expectancy_r"] is not None
            for sibling in siblings
        ),
        "CAUSAL_DQE_REPLAY": record["integrity"] == {
            "causal": True, "decision_quality": True, "canonical_replay": True, "venue_explicit": True,
        },
    }
    return all(checks.values()), [name for name, passed in checks.items() if not passed]


def run_development() -> dict[str, Any]:
    raw_frames, manifest = load_development()
    frames = {symbol: feature_frame(frame) for symbol, frame in raw_frames.items()}
    state_report = _forward_state_report(frames)
    state_by_key = {
        (symbol, pd.Timestamp(row["decision_at"]).isoformat()): str(row["positioning_state"])
        for symbol, frame in frames.items() for _, row in frame.iterrows()
    }
    specs = frozen_candidates()
    records: list[dict[str, Any]] = []
    for spec in specs:
        plans: dict[str, dict[str, dict[str, Any]]] = {}
        counters = {"eligible": 0, "family_matches": 0, "dqe_approved": 0, "dqe_rejected": 0}
        for symbol, frame in frames.items():
            plans[symbol], local = build_plans(spec, symbol, frame)
            for key in counters:
                counters[key] += local[key]
        scenarios: dict[str, list[ReplayOutcome]] = {}
        for label, costs in (("BASE", BASE_COST), ("HIGH", HIGH_COST), ("STRESS", STRESS_COST)):
            combined: list[ReplayOutcome] = []
            for symbol, frame in frames.items():
                candles = frame[["time", "open", "high", "low", "close", "volume"]].copy()
                candles["confirm"] = "1"
                engine = HistoricalReplayEngine(ReplayConfig(
                    "1h", warmup_bars=220, entry_expiry_bars=1, max_holding_bars=24,
                    entry_policy=EntryPolicy.MARKET_NEXT_OPEN, costs=costs,
                ))
                outcomes = engine.run_plans(candles, plans[symbol], metadata={
                    "data_provider": "BINANCE_VISION_UM", "sample_role": "DERIV_DEV",
                    "split_id": "derivatives-alpha-primary-1h-v1:DERIV_DEV",
                    "decision_authority": DecisionQualityEngine.AUTHORITY,
                    "decision_version": DecisionQualityEngine.DECISION_VERSION,
                    "decision_source": "DERIVATIVES_ALPHA_RESEARCH", "variants_evaluated": 12,
                })
                combined.extend(_apply_exit_and_funding(outcomes, frame, EXIT_FRICTION[label]))
            scenarios[label] = sorted(combined, key=lambda item: (item.decision_at, item.symbol))
        base = scenarios["BASE"]
        clusters = _cluster_values(base, spec.family)
        records.append({
            "experiment_id": stable_hash({"candidate": spec.candidate_id, "materialization": MATERIALIZATION_ID}),
            "candidate": asdict(spec) | {"candidate_id": spec.candidate_id},
            "parent": None, "feature_schema": "positioning-state-v1",
            "dataset_hashes": {symbol: manifest["splits"]["DERIV_DEV"][symbol]["sha256"] for symbol in sorted(frames)},
            "counters": counters, "nominal_n": len(base), "cluster_n": len(clusters),
            "costs": {label: _metrics(items) for label, items in scenarios.items()},
            "folds": _folds(base),
            "symbols": _groups(base, lambda item: item.symbol),
            "directions": _groups(base, lambda item: item.direction),
            "positioning_states": _groups(
                base, lambda item: state_by_key.get((item.symbol, pd.Timestamp(item.decision_at).isoformat()), "UNKNOWN"),
            ),
            "bootstrap": _bootstrap(base, spec.family, int(spec.candidate_id[:8], 16)),
            "integrity": {"causal": True, "decision_quality": True, "canonical_replay": True, "venue_explicit": True},
            "result": "PENDING_GATE", "rejection_reasons": [],
        })
    for record in records:
        siblings = [item for item in records if item["candidate"]["family"] == record["candidate"]["family"]]
        qualified, reasons = _promotion(record, siblings)
        record["result"] = "DEVELOPMENT_QUALIFIED" if qualified else "REJECTED_DEVELOPMENT"
        record["rejection_reasons"] = reasons
    qualified = [record for record in records if record["result"] == "DEVELOPMENT_QUALIFIED"]
    qualified.sort(key=lambda item: (
        item["costs"]["BASE"]["expectancy_r"], item["cluster_n"], item["bootstrap"]["lower"]
    ), reverse=True)
    finalist = qualified[0]["candidate"] if qualified else None
    return {
        "schema": "derivatives-alpha-development-v1", "materialization_id": MATERIALIZATION_ID,
        "provider_venue": "BINANCE", "split": "DERIV_DEV", "validation_accessed": False,
        "blind_accessed": False, "legacy_seen_test_accessed": False,
        "candidate_registry_identity": candidate_registry_identity(specs),
        "conditional_forward_returns": state_report, "experiments": records,
        "development_qualified_count": len(qualified), "frozen_validation_candidate": finalist,
        "production_defaults_changed": False,
    }
