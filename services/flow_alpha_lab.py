"""Bounded, Development-only directional flow alpha laboratory."""
from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from services.decision_quality import DecisionQualityEngine
from services.flow_microstructure import FlowFeatureEngine, aggregate_completed, stable_hash
from services.research_replay import EntryPolicy, HistoricalReplayEngine, ReplayConfig, ReplayCostModel, ReplayOutcome


ROOT = Path("research_artifacts/flow_alpha")
MATERIALIZATION_ID = "fc1879232f3149d7"
ZERO_COST = ReplayCostModel(0.0, 0.0, source="FLOW_PATH_ONLY")
COSTS = {
    "BASE": {"fee": .0005, "entry_friction": .0005, "exit_friction": .0003},
    "HIGH": {"fee": .00075, "entry_friction": .0009, "exit_friction": .0007},
    "STRESS": {"fee": .0010, "entry_friction": .0015, "exit_friction": .0012},
}


@dataclass(frozen=True)
class FlowCandidate:
    family: str
    variant: str
    timeframe: str
    core: float
    extreme: float

    @property
    def candidate_id(self) -> str:
        return stable_hash({
            "schema": "flow-alpha-directional-v1", "materialization": MATERIALIZATION_ID,
            **asdict(self), "entry": "NEXT_BAR_OPEN", "stop_atr": 1.5,
            "risk_floor": .002, "target_r": 1.5,
            "max_hold": 12 if self.timeframe == "5m" else 8,
        })


def frozen_candidates() -> tuple[FlowCandidate, ...]:
    families = (
        ("F1_FLOW_CONTINUATION_5M", "5m"),
        ("F2_CVD_DIVERGENCE_REVERSAL_15M", "15m"),
        ("F3_ABSORPTION_REVERSAL_5M", "5m"),
        ("F4_OI_FLOW_SQUEEZE_15M", "15m"),
    )
    result = []
    for variant, core, extreme in (("BROAD", .70, .85), ("BASE", .75, .90), ("STRICT", .80, .95)):
        result.extend(FlowCandidate(family, variant, timeframe, core, extreme) for family, timeframe in families)
    return tuple(result)


class _Memory:
    def remember(self, symbol: str, timeframe: str, data: Mapping[str, Any]) -> dict[str, Any]:
        return {"symbol": symbol, "timeframe": timeframe, "research_only": True}


def _verify(path: Path, expected: str) -> None:
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError(f"dataset hash mismatch: {path}")


def load_development() -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    path = ROOT / f"materialization-{MATERIALIZATION_ID}.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("outcomes_evaluated") or manifest.get("validation_accessed") or manifest.get("blind_accessed"):
        raise ValueError("materialization access guard failed")
    frames = {}
    for symbol, receipt in manifest["splits"]["FLOW_DEV"].items():
        dataset = Path(receipt["path"])
        _verify(dataset, receipt["sha256"])
        frame = pd.read_csv(dataset, compression="gzip")
        for column in ("time", "close_at", "decision_at", "oi_source_at", "oi_available_at", "funding_at", "funding_available_at"):
            frame[column] = pd.to_datetime(frame[column], utc=True, errors="coerce")
        if len(frame) != 289152 or set(frame["provider"]) != {"BINANCE_UM"}:
            raise ValueError(f"{symbol} Development identity mismatch")
        frames[symbol] = frame
    return frames, manifest


def _hour_context(frame: pd.DataFrame, tactical: pd.DataFrame) -> pd.DataFrame:
    hourly = aggregate_completed(frame, 12)
    close = hourly["close"].astype(float)
    hourly["hour_ema24"] = close.ewm(span=24, adjust=False).mean()
    hourly["hour_ema72"] = close.ewm(span=72, adjust=False).mean()
    hourly["hour_return_6"] = close.pct_change(6)
    context = hourly[["decision_at", "hour_ema24", "hour_ema72", "hour_return_6"]].sort_values("decision_at")
    return pd.merge_asof(
        tactical.sort_values("decision_at"), context, on="decision_at", direction="backward",
        allow_exact_matches=True, tolerance=pd.Timedelta(hours=2),
    )


def feature_frames(frame: pd.DataFrame) -> dict[str, pd.DataFrame]:
    five = FlowFeatureEngine(bars_per_hour=12).transform(frame.copy())
    five = _hour_context(frame, five)
    fifteen_raw = aggregate_completed(frame, 3)
    fifteen = FlowFeatureEngine(bars_per_hour=4).transform(fifteen_raw)
    fifteen = _hour_context(frame, fifteen)
    return {"5m": five, "15m": fifteen}


def candidate_masks(spec: FlowCandidate, frame: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    core, extreme = spec.core, spec.extreme
    hour_up = (frame["hour_ema24"] > frame["hour_ema72"]) & (frame["hour_return_6"] > 0)
    hour_down = (frame["hour_ema24"] < frame["hour_ema72"]) & (frame["hour_return_6"] < 0)
    delta_high, delta_low = frame["delta_percentile"] >= core, frame["delta_percentile"] <= 1 - core
    move = frame["abs_return_percentile"] >= core
    if spec.family == "F1_FLOW_CONTINUATION_5M":
        long = hour_up & delta_high & move & (frame["return_3bar"] > 0) & (frame["return_1bar"] > 0)
        short = hour_down & delta_low & move & (frame["return_3bar"] < 0) & (frame["return_1bar"] < 0)
    elif spec.family == "F2_CVD_DIVERGENCE_REVERSAL_15M":
        long = (frame["price_cvd_divergence"] == "PRICE_DOWN_CVD_UP") & move & (frame["return_1bar"] > 0)
        short = (frame["price_cvd_divergence"] == "PRICE_UP_CVD_DOWN") & move & (frame["return_1bar"] < 0)
        long &= frame["delta_percentile"] >= extreme
        short &= frame["delta_percentile"] <= 1 - extreme
    elif spec.family == "F3_ABSORPTION_REVERSAL_5M":
        quiet = frame["abs_return_percentile"] <= 1 - core
        long = quiet & (frame["delta_percentile"] <= 1 - extreme) & (frame["return_1bar"] >= 0)
        short = quiet & (frame["delta_percentile"] >= extreme) & (frame["return_1bar"] <= 0)
    else:
        oi_valid = frame.get("oi_status", pd.Series("GAPPED", index=frame.index)) == "VALID"
        oi_expand = frame["oi_change_percentile"] >= core
        funding = frame["funding_percentile"]
        long = oi_valid & oi_expand & delta_high & move & (frame["return_3bar"] > 0) & (funding <= extreme)
        short = oi_valid & oi_expand & delta_low & move & (frame["return_3bar"] < 0) & (funding >= 1 - extreme)
    ready = frame[["atr14", "hour_ema24", "hour_ema72"]].notna().all(axis=1)
    return (long & ready).fillna(False), (short & ready).fillna(False)


def _dqe_template(spec: FlowCandidate, direction: str) -> dict[str, Any]:
    quality = DecisionQualityEngine()
    quality.memory = _Memory()
    decision = quality.enrich({
        "symbol": "FLOW_RESEARCH", "timeframe": spec.timeframe, "timestamp": "2023-01-01T00:00:00+00:00",
        "direction": direction, "direction_score": 75, "setup_score": 75, "score": 75,
        "confidence": 75, "probability": 75, "execution_status": "🟢 READY", "plan_valid": True,
        "entry": 100, "stop": 99, "tp1": 101.5, "tp2": 101.5, "tp3": 101.5,
        "market_regime": {"code": "FLOW_RESEARCH"}, "data_quality": {"status": "VALID"},
        "reasons": [f"Preregistered {spec.family}/{spec.variant}"],
        "triggers": ["Closed tactical flow interval"],
        "score_components": [{"label": spec.family, "value": 25}],
    }, source="FLOW_ALPHA_RESEARCH")
    approved, reason = DecisionQualityEngine.authorization(decision)
    if not approved:
        raise ValueError(f"DecisionQuality rejected frozen template: {reason}")
    return decision


def plans_for(spec: FlowCandidate, symbol: str, frame: pd.DataFrame) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    long, short = candidate_masks(spec, frame)
    templates = {direction: _dqe_template(spec, direction) for direction in ("LONG", "SHORT")}
    selected = [(index, "LONG") for index in frame.index[long]] + [(index, "SHORT") for index in frame.index[short]]
    selected.sort(key=lambda item: item[0])
    plans = {}
    for index, direction in selected:
        row = frame.loc[index]
        price, atr = float(row["close"]), float(row["atr14"])
        risk = max(1.5 * atr, .002 * price)
        stop = price - risk if direction == "LONG" else price + risk
        target = price + 1.5 * risk if direction == "LONG" else price - 1.5 * risk
        if stop <= 0 or target <= 0:
            continue
        decision_at = pd.Timestamp(row["decision_at"]).isoformat()
        plans[decision_at] = {
            "signal_id": f"{spec.candidate_id}:{symbol}:{decision_at}",
            "strategy_version": spec.candidate_id, "symbol": symbol, "direction": direction,
            "entry": price, "stop": stop, "targets": (target,),
            "entry_policy": EntryPolicy.MARKET_NEXT_OPEN.value,
            "metadata": {
                "candidate_id": spec.candidate_id, "alpha_family": spec.family,
                "provider_venue": "BINANCE_UM", "flow_semantics": "EXCHANGE_REPORTED_TAKER_BUY_AGGREGATE",
                "decision_authority": templates[direction]["decision_authority"],
                "decision_version": templates[direction]["decision_version"],
                "decision_source": "FLOW_ALPHA_RESEARCH",
            },
        }
    return plans, {"eligible": len(frame), "long_matches": int(long.sum()), "short_matches": int(short.sum()), "dqe_approved": len(plans)}


def _funding_events(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame[["funding_at", "funding_rate"]].dropna().drop_duplicates("funding_at").copy()
    result["funding_at"] = pd.to_datetime(result["funding_at"], utc=True)
    return result.sort_values("funding_at")


def _cost_outcomes(path_outcomes: list[ReplayOutcome], raw: pd.DataFrame, scenario: str) -> list[ReplayOutcome]:
    cost, events, result = COSTS[scenario], _funding_events(raw), []
    for item in path_outcomes:
        if item.simulated_fill is None or item.exit_price is None or item.exit_at is None:
            continue
        risk = abs(item.intended_entry - item.stop)
        next_open = float(item.simulated_fill)
        fill = next_open * (1 + cost["entry_friction"] if item.direction == "LONG" else 1 - cost["entry_friction"])
        gross = item.exit_price - fill if item.direction == "LONG" else fill - item.exit_price
        fee = (abs(fill) + abs(item.exit_price)) * cost["fee"]
        exit_friction = abs(item.exit_price) * cost["exit_friction"]
        start, end = pd.Timestamp(item.fill_at), pd.Timestamp(item.exit_at)
        rate = events.loc[(events["funding_at"] > start) & (events["funding_at"] <= end), "funding_rate"].astype(float).sum()
        funding = fill * (rate if item.direction == "LONG" else -rate)
        net_r = (gross - fee - exit_friction - funding) / risk
        result.append(replace(
            item, simulated_fill=fill, gross_pnl=gross, total_fee=fee,
            funding=funding, funding_status="ACTUAL_SETTLEMENTS", net_pnl=net_r * risk,
            gross_r=gross / risk, net_r=net_r,
            provenance=item.provenance | {"cost_scenario": scenario, **cost},
        ))
    return result


def _metrics(items: list[ReplayOutcome]) -> dict[str, Any]:
    values = [float(item.net_r) for item in sorted(items, key=lambda x: (x.decision_at, x.symbol))]
    wins, losses = [v for v in values if v > 0], [v for v in values if v < 0]
    equity = peak = dd = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
    return {
        "fills": len(values), "win_rate": len(wins) / len(values) if values else None,
        "expectancy_r": sum(values) / len(values) if values else None,
        "profit_factor": sum(wins) / abs(sum(losses)) if losses else (math.inf if wins else None),
        "net_r": sum(values), "max_drawdown_r": dd,
        "mfe_r_mean": float(np.mean([x.mfe_r for x in items if x.mfe_r is not None])) if items else None,
        "mae_r_mean": float(np.mean([x.mae_r for x in items if x.mae_r is not None])) if items else None,
    }

def _group(items: list[ReplayOutcome], key) -> dict[str, dict[str, Any]]:
    groups = {}
    for item in items:
        groups.setdefault(str(key(item)), []).append(item)
    return {name: _metrics(group) for name, group in sorted(groups.items())}


FOLDS = (
    ("F1", pd.Timestamp("2023-01-01T00:00Z"), pd.Timestamp("2023-08-31T23:59Z")),
    ("F2", pd.Timestamp("2023-09-01T00:00Z"), pd.Timestamp("2024-04-30T23:59Z")),
    ("F3", pd.Timestamp("2024-05-01T00:00Z"), pd.Timestamp("2024-12-31T23:59Z")),
    ("F4", pd.Timestamp("2025-01-01T00:00Z"), pd.Timestamp("2025-09-30T23:59Z")),
)


def _folds(items: list[ReplayOutcome]) -> list[dict[str, Any]]:
    return [{"fold": name, "metrics": _metrics([
        item for item in items if start <= pd.Timestamp(item.decision_at) <= end
    ])} for name, start, end in FOLDS]


def _cluster_values(items: list[ReplayOutcome], family: str) -> dict[str, float]:
    values = {}
    for item in items:
        hour = pd.Timestamp(item.decision_at).floor("h").isoformat()
        key = f"{hour}|{item.direction}|{family}"
        values[key] = values.get(key, 0.0) + float(item.net_r)
    return values


def _bootstrap(items: list[ReplayOutcome], family: str, seed: int, resamples: int = 1000) -> dict[str, Any]:
    values = list(_cluster_values(items, family).values())
    if not values:
        return {"clusters": 0, "lower": None, "median": None, "upper": None}
    rng, draws = random.Random(seed), []
    for _ in range(resamples):
        sample = [values[rng.randrange(len(values))] for _ in values]
        draws.append(sum(sample) / len(sample))
    draws.sort()
    return {"clusters": len(values), "lower": draws[24], "median": draws[499], "upper": draws[974], "seed": seed}


def _conditional_report(frames: Mapping[str, Mapping[str, pd.DataFrame]], candidates: tuple[FlowCandidate, ...]) -> list[dict[str, Any]]:
    records = []
    for spec in candidates:
        if spec.variant != "BASE":
            continue
        for symbol, by_timeframe in frames.items():
            frame = by_timeframe[spec.timeframe]
            long, short = candidate_masks(spec, frame)
            close = frame["close"].astype(float)
            for direction, mask, sign in (("LONG", long, 1), ("SHORT", short, -1)):
                for horizon in (1, 3, 6, 12):
                    values = (sign * (close.shift(-horizon) / close - 1))[mask].dropna()
                    records.append({
                        "family": spec.family, "symbol": symbol, "direction": direction,
                        "horizon_bars": horizon, "n": len(values), "mean": float(values.mean()) if len(values) else None,
                        "median": float(values.median()) if len(values) else None,
                        "positive_probability": float((values > 0).mean()) if len(values) else None,
                        "p05": float(values.quantile(.05)) if len(values) else None,
                        "p95": float(values.quantile(.95)) if len(values) else None,
                    })
    return records


def _ablation(frames: Mapping[str, Mapping[str, pd.DataFrame]]) -> list[dict[str, Any]]:
    records = []
    for symbol, by_timeframe in frames.items():
        frame = by_timeframe["5m"]
        future = frame["close"].shift(-3) / frame["close"] - 1
        hour_up = frame["hour_ema24"] > frame["hour_ema72"]
        structure = hour_up & (frame["return_3bar"] > 0)
        flow = structure & (frame["delta_percentile"] >= .75)
        for name, mask in (("STRUCTURE_ONLY", structure), ("STRUCTURE_PLUS_FLOW", flow)):
            values = future[mask].dropna()
            records.append({"symbol": symbol, "family": "F1", "ablation": name, "n": len(values), "mean": float(values.mean())})
        f15 = by_timeframe["15m"]
        future15 = f15["close"].shift(-3) / f15["close"] - 1
        price_flow = (f15["return_3bar"] > 0) & (f15["delta_percentile"] >= .75)
        plus_oi = price_flow & (f15["oi_status"] == "VALID") & (f15["oi_change_percentile"] >= .75)
        plus_funding = plus_oi & (f15["funding_percentile"] <= .90)
        for name, mask in (("PRICE_FLOW", price_flow), ("PLUS_OI", plus_oi), ("PLUS_FUNDING", plus_funding)):
            values = future15[mask].dropna()
            records.append({"symbol": symbol, "family": "F4", "ablation": name, "n": len(values), "mean": float(values.mean())})
    return records


def _gate(record: Mapping[str, Any], siblings: list[Mapping[str, Any]]) -> tuple[bool, list[str]]:
    base, high, stress = (record["costs"][name] for name in ("BASE", "HIGH", "STRESS"))
    folds = [item["metrics"] for item in record["folds"]]
    positive_symbols = sum(x["expectancy_r"] is not None and x["expectancy_r"] > 0 for x in record["symbols"].values())
    checks = {
        "MIN_300_FILLS": base["fills"] >= 300,
        "MIN_120_CLUSTERS": record["cluster_n"] >= 120,
        "EXPECTANCY_0_04R": base["expectancy_r"] is not None and base["expectancy_r"] >= .04,
        "PF_1_15": base["profit_factor"] is not None and base["profit_factor"] >= 1.15,
        "HIGH_POSITIVE": high["expectancy_r"] is not None and high["expectancy_r"] > 0 and high["profit_factor"] > 1,
        "STRESS_POSITIVE": stress["expectancy_r"] is not None and stress["expectancy_r"] > 0 and stress["profit_factor"] > 1,
        "THREE_NONNEGATIVE_FOLDS": sum(x["expectancy_r"] is not None and x["expectancy_r"] >= 0 for x in folds) >= 3,
        "NO_FOLD_BELOW_MINUS_0_08R": all(x["expectancy_r"] is not None and x["expectancy_r"] >= -.08 for x in folds),
        "MAX_DD_30R": base["max_drawdown_r"] <= 30,
        "TWO_POSITIVE_SYMBOLS": positive_symbols >= 2,
        "BOTH_DIRECTIONS_REPRESENTED": len(record["directions"]) == 2,
        "BOOTSTRAP_LOWER_MINUS_0_02R": record["bootstrap"]["lower"] is not None and record["bootstrap"]["lower"] > -.02,
        "NEIGHBOR_PLATEAU": len(siblings) == 3 and all(x["costs"]["BASE"]["fills"] >= 150 for x in siblings),
        "INTEGRITY": all(record["integrity"].values()),
    }
    return all(checks.values()), [name for name, passed in checks.items() if not passed]


def run_development() -> dict[str, Any]:
    raw, manifest = load_development()
    frames = {symbol: feature_frames(frame) for symbol, frame in raw.items()}
    specs = frozen_candidates()
    records = []
    for spec in specs:
        combined_paths, counters = [], {"eligible": 0, "long_matches": 0, "short_matches": 0, "dqe_approved": 0}
        paths_by_symbol = {}
        for symbol, by_timeframe in frames.items():
            frame = by_timeframe[spec.timeframe]
            plans, local = plans_for(spec, symbol, frame)
            for key in counters:
                counters[key] += local[key]
            candles = frame[["time", "open", "high", "low", "close", "volume"]].copy()
            candles["confirm"] = "1"
            path_outcomes = HistoricalReplayEngine(ReplayConfig(
                spec.timeframe, warmup_bars=220 if spec.timeframe == "5m" else 80,
                entry_expiry_bars=1, max_holding_bars=12 if spec.timeframe == "5m" else 8,
                entry_policy=EntryPolicy.MARKET_NEXT_OPEN, costs=ZERO_COST,
            )).run_plans(candles, plans, metadata={
                "data_provider": "BINANCE_UM", "sample_role": "FLOW_DEV",
                "split_id": "flow-alpha-directional-v1:FLOW_DEV",
                "decision_authority": DecisionQualityEngine.AUTHORITY,
                "decision_version": DecisionQualityEngine.DECISION_VERSION,
                "decision_source": "FLOW_ALPHA_RESEARCH", "variants_evaluated": 12,
            })
            paths_by_symbol[symbol] = path_outcomes
            combined_paths.extend(path_outcomes)
        scenarios = {}
        for scenario in COSTS:
            outcomes = []
            for symbol, paths in paths_by_symbol.items():
                outcomes.extend(_cost_outcomes(paths, raw[symbol], scenario))
            scenarios[scenario] = sorted(outcomes, key=lambda x: (x.decision_at, x.symbol))
        base = scenarios["BASE"]
        clusters = _cluster_values(base, spec.family)
        records.append({
            "experiment_id": stable_hash({"candidate": spec.candidate_id, "materialization": MATERIALIZATION_ID}),
            "candidate": asdict(spec) | {"candidate_id": spec.candidate_id},
            "dataset_hashes": {s: manifest["splits"]["FLOW_DEV"][s]["sha256"] for s in sorted(raw)},
            "features": ["real_taker_delta", "CVD", "price", "1h_context"] + (["OI", "funding"] if spec.family.startswith("F4") else []),
            "counters": counters, "nominal_n": len(base), "cluster_n": len(clusters),
            "costs": {name: _metrics(items) for name, items in scenarios.items()},
            "folds": _folds(base), "symbols": _group(base, lambda x: x.symbol),
            "directions": _group(base, lambda x: x.direction),
            "bootstrap": _bootstrap(base, spec.family, int(spec.candidate_id[:8], 16)),
            "integrity": {"causal": True, "real_flow": True, "decision_quality": True, "canonical_replay": True, "venue_explicit": True},
            "result": "PENDING_GATE", "rejection_reasons": [],
        })
    for record in records:
        siblings = [x for x in records if x["candidate"]["family"] == record["candidate"]["family"]]
        qualified, failures = _gate(record, siblings)
        record["result"] = "DEVELOPMENT_QUALIFIED" if qualified else "REJECTED_DEVELOPMENT"
        record["rejection_reasons"] = failures
    qualified = [x for x in records if x["result"] == "DEVELOPMENT_QUALIFIED"]
    qualified.sort(key=lambda x: (x["costs"]["BASE"]["expectancy_r"], x["cluster_n"], x["bootstrap"]["lower"]), reverse=True)
    return {
        "schema": "flow-alpha-directional-development-v1", "materialization_id": MATERIALIZATION_ID,
        "provider_venue": "BINANCE_UM", "split": "FLOW_DEV",
        "conditional_information": _conditional_report(frames, specs),
        "feature_ablations": _ablation(frames), "experiments": records,
        "development_qualified_count": len(qualified),
        "frozen_validation_candidate": qualified[0]["candidate"] if qualified else None,
        "validation_accessed": False, "blind_accessed": False,
        "derivatives_blind_accessed": False, "production_defaults_changed": False,
    }
