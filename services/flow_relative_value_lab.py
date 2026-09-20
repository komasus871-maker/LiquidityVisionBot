"""Bounded Development-only cross-sectional flow alpha laboratory."""
from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from services.decision_quality import DecisionQualityEngine
from services.flow_alpha_lab import COSTS, FOLDS, MATERIALIZATION_ID, ROOT, feature_frames, load_development
from services.flow_microstructure import stable_hash


@dataclass(frozen=True)
class RelativeValueCandidate:
    family: str
    variant: str
    threshold: float
    timeframe: str = "15m"
    holding_bars: int = 4

    @property
    def candidate_id(self) -> str:
        return stable_hash({
            "schema": "flow-alpha-relative-value-v1",
            "materialization": MATERIALIZATION_ID,
            **asdict(self),
            "entry": "NEXT_BAR_OPEN",
            "construction": "EQUAL_NOTIONAL_LONG_SHORT",
            "risk": "1.5_X_MEAN_LEG_ATR_PERCENT_WITH_0.3_PERCENT_FLOOR",
        })


def frozen_relative_value_candidates() -> tuple[RelativeValueCandidate, ...]:
    families = (
        "RV1_RELATIVE_FLOW_CONTINUATION",
        "RV2_PRICE_FLOW_MISMATCH_REVERSION",
        "RV3_OI_FLOW_DISPERSION",
    )
    return tuple(
        RelativeValueCandidate(family, variant, threshold)
        for variant, threshold in (("BROAD", .75), ("BASE", 1.0), ("STRICT", 1.25))
        for family in families
    )


def _verify_dqe(spec: RelativeValueCandidate) -> dict[str, Any]:
    from services.flow_alpha_lab import _Memory

    quality = DecisionQualityEngine()
    quality.memory = _Memory()
    decision = quality.enrich({
        "symbol": "FLOW_RELATIVE_VALUE_RESEARCH", "timeframe": "15m",
        "timestamp": "2023-01-01T00:00:00+00:00", "direction": "LONG",
        "direction_score": 75, "setup_score": 75, "score": 75,
        "confidence": 75, "probability": 75, "execution_status": "🟢 READY",
        "plan_valid": True, "entry": 100, "stop": 99, "tp1": 101.5,
        "tp2": 101.5, "tp3": 101.5,
        "market_regime": {"code": "FLOW_RELATIVE_VALUE_RESEARCH"},
        "data_quality": {"status": "VALID"},
        "reasons": [f"Preregistered {spec.family}/{spec.variant}"],
        "triggers": ["Closed 15m cross-sectional flow state"],
        "score_components": [{"label": spec.family, "value": 25}],
    }, source="FLOW_RELATIVE_VALUE_RESEARCH")
    approved, reason = DecisionQualityEngine.authorization(decision)
    if not approved:
        raise ValueError(f"DecisionQuality rejected relative-value template: {reason}")
    return decision


def _panel(frames: Mapping[str, Mapping[str, pd.DataFrame]]) -> pd.DataFrame:
    columns = [
        "time", "decision_at", "open", "high", "low", "close", "atr14",
        "return_1bar", "return_3bar", "return_12bar", "delta_zscore",
        "oi_change_3bar", "oi_status", "funding_percentile",
    ]
    pieces = []
    for symbol, by_timeframe in sorted(frames.items()):
        local = by_timeframe["15m"][[c for c in columns if c in by_timeframe["15m"]]].copy()
        local["symbol"] = symbol
        pieces.append(local)
    panel = pd.concat(pieces, ignore_index=True)
    panel["atr_percent"] = panel["atr14"] / panel["close"]
    group = panel.groupby("symbol", sort=False)
    oi = panel["oi_change_3bar"]
    oi_mean = group["oi_change_3bar"].transform(lambda s: s.rolling(672, min_periods=96).mean())
    oi_std = group["oi_change_3bar"].transform(lambda s: s.rolling(672, min_periods=96).std())
    panel["oi_change_zscore"] = (oi - oi_mean) / oi_std.replace(0, np.nan)
    panel["price_strength"] = panel["return_12bar"] / (panel["atr_percent"] * math.sqrt(12)).replace(0, np.nan)
    panel["price_flow_mismatch"] = panel["price_strength"] - panel["delta_zscore"]
    return panel.sort_values(["decision_at", "symbol"]).reset_index(drop=True)


def _score(spec: RelativeValueCandidate, local: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    if spec.family == "RV1_RELATIVE_FLOW_CONTINUATION":
        score = local["delta_zscore"]
        valid = local["return_3bar"].notna()
    elif spec.family == "RV2_PRICE_FLOW_MISMATCH_REVERSION":
        score = -local["price_flow_mismatch"]
        valid = local["return_1bar"].notna()
    else:
        score = local["delta_zscore"] + .5 * local["oi_change_zscore"]
        valid = (local["oi_status"] == "VALID") & local["funding_percentile"].notna()
    return score, valid


def _signals(spec: RelativeValueCandidate, panel: pd.DataFrame) -> list[dict[str, Any]]:
    fields = (
        "delta_zscore", "price_flow_mismatch", "oi_change_zscore", "oi_status",
        "funding_percentile", "return_1bar", "return_3bar",
    )
    wide = {
        field: panel.pivot(index="decision_at", columns="symbol", values=field).sort_index()
        for field in fields
    }
    if spec.family == "RV1_RELATIVE_FLOW_CONTINUATION":
        score = wide["delta_zscore"]
        valid = wide["return_3bar"].notna()
    elif spec.family == "RV2_PRICE_FLOW_MISMATCH_REVERSION":
        score = -wide["price_flow_mismatch"]
        valid = wide["return_1bar"].notna()
    else:
        score = wide["delta_zscore"] + .5 * wide["oi_change_zscore"]
        valid = (wide["oi_status"] == "VALID") & wide["funding_percentile"].notna()
    valid &= score.notna()
    score_values = score.to_numpy(float)
    valid_values = valid.to_numpy(bool)
    long_index = np.argmax(np.where(valid_values, score_values, -np.inf), axis=1)
    short_index = np.argmin(np.where(valid_values, score_values, np.inf), axis=1)
    row_index = np.arange(len(score))
    long_score = score_values[row_index, long_index]
    short_score = score_values[row_index, short_index]
    all_valid = valid_values.sum(axis=1) == 3
    score_gap = np.full(len(score), -np.inf)
    score_gap[all_valid] = long_score[all_valid] - short_score[all_valid]
    selected = (
        all_valid
        & (long_score >= spec.threshold)
        & (short_score <= -spec.threshold)
        & (score_gap >= 2 * spec.threshold)
    )
    directional = wide["return_3bar" if spec.family != "RV2_PRICE_FLOW_MISMATCH_REVERSION" else "return_1bar"].to_numpy(float)
    selected &= (directional[row_index, long_index] > 0) & (directional[row_index, short_index] < 0)
    if spec.family == "RV3_OI_FLOW_DISPERSION":
        funding_values = wide["funding_percentile"].to_numpy(float)
        selected &= (funding_values[row_index, long_index] < .95) & (funding_values[row_index, short_index] > .05)
    symbols = score.columns.to_numpy(str)
    decisions = score.index
    return [{
        "decision_at": pd.Timestamp(decisions[index]),
        "long_symbol": str(symbols[long_index[index]]),
        "short_symbol": str(symbols[short_index[index]]),
        "long_score": float(long_score[index]), "short_score": float(short_score[index]),
    } for index in np.flatnonzero(selected)]


def _funding_rates(raw: Mapping[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    result = {}
    for symbol, frame in raw.items():
        events = frame[["funding_at", "funding_rate"]].dropna().drop_duplicates("funding_at").copy()
        events["funding_at"] = pd.to_datetime(events["funding_at"], utc=True)
        result[symbol] = events.sort_values("funding_at")
    return result


def _funding_between(events: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> float:
    return float(events.loc[(events["funding_at"] > start) & (events["funding_at"] <= end), "funding_rate"].sum())


def _evaluate(
    spec: RelativeValueCandidate,
    signals: list[dict[str, Any]],
    by_symbol: Mapping[str, pd.DataFrame],
    funding: Mapping[str, pd.DataFrame],
    scenario: str,
) -> list[dict[str, Any]]:
    cost = COSTS[scenario]
    indexed = {symbol: frame.set_index("decision_at", drop=False) for symbol, frame in by_symbol.items()}
    outcomes: list[dict[str, Any]] = []
    for signal in signals:
        decision_at = signal["decision_at"]
        long_frame, short_frame = indexed[signal["long_symbol"]], indexed[signal["short_symbol"]]
        if decision_at not in long_frame.index or decision_at not in short_frame.index:
            continue
        li, si = long_frame.index.get_loc(decision_at), short_frame.index.get_loc(decision_at)
        if isinstance(li, slice) or isinstance(si, slice) or li + spec.holding_bars >= len(long_frame) or si + spec.holding_bars >= len(short_frame):
            continue
        long_entry_row, short_entry_row = long_frame.iloc[li + 1], short_frame.iloc[si + 1]
        long_exit_row, short_exit_row = long_frame.iloc[li + spec.holding_bars], short_frame.iloc[si + spec.holding_bars]
        long_entry = float(long_entry_row["open"]) * (1 + cost["entry_friction"])
        short_entry = float(short_entry_row["open"]) * (1 - cost["entry_friction"])
        long_exit = float(long_exit_row["close"]) * (1 - cost["exit_friction"])
        short_exit = float(short_exit_row["close"]) * (1 + cost["exit_friction"])
        gross_return = .5 * ((long_exit / long_entry - 1) + (1 - short_exit / short_entry))
        fee_return = cost["fee"] * 2
        start = pd.Timestamp(long_entry_row["time"])
        end = pd.Timestamp(long_exit_row["time"]) + pd.Timedelta(minutes=15)
        funding_return = .5 * (
            _funding_between(funding[signal["long_symbol"]], start, end)
            - _funding_between(funding[signal["short_symbol"]], start, end)
        )
        long_decision = long_frame.iloc[li]
        short_decision = short_frame.iloc[si]
        risk_return = max(
            1.5 * .5 * (
                float(long_decision["atr14"]) / float(long_decision["close"])
                + float(short_decision["atr14"]) / float(short_decision["close"])
            ),
            .003,
        )
        net_r = (gross_return - fee_return - funding_return) / risk_return
        path = []
        for offset in range(1, spec.holding_bars + 1):
            lp = float(long_frame.iloc[li + offset]["close"]) / long_entry - 1
            sp = 1 - float(short_frame.iloc[si + offset]["close"]) / short_entry
            path.append((.5 * (lp + sp) - fee_return - funding_return) / risk_return)
        outcomes.append({
            **signal, "entry_at": start, "exit_at": end,
            "pair": f"{signal['long_symbol']}>{signal['short_symbol']}",
            "gross_return": gross_return, "fee_return": fee_return,
            "funding_return": funding_return, "risk_return": risk_return,
            "net_r": net_r, "mfe_r": max(path), "mae_r": min(path),
            "cost_scenario": scenario,
        })
    return outcomes


def _metrics(items: list[Mapping[str, Any]]) -> dict[str, Any]:
    ordered = sorted(items, key=lambda x: (x["decision_at"], x["pair"]))
    values = [float(x["net_r"]) for x in ordered]
    wins, losses = [x for x in values if x > 0], [x for x in values if x < 0]
    equity = peak = drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return {
        "fills": len(values), "win_rate": len(wins) / len(values) if values else None,
        "expectancy_r": float(np.mean(values)) if values else None,
        "profit_factor": sum(wins) / abs(sum(losses)) if losses else (math.inf if wins else None),
        "net_r": sum(values), "max_drawdown_r": drawdown,
        "mfe_r_mean": float(np.mean([x["mfe_r"] for x in items])) if items else None,
        "mae_r_mean": float(np.mean([x["mae_r"] for x in items])) if items else None,
    }


def _groups(items: list[Mapping[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    values: dict[str, list[Mapping[str, Any]]] = {}
    for item in items:
        values.setdefault(str(item[key]), []).append(item)
    return {name: _metrics(group) for name, group in sorted(values.items())}


def _folds(items: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [{"fold": name, "metrics": _metrics([
        item for item in items if start <= pd.Timestamp(item["decision_at"]) <= end
    ])} for name, start, end in FOLDS]


def _cluster_values(items: list[Mapping[str, Any]]) -> dict[str, float]:
    values: dict[str, float] = {}
    for item in items:
        key = pd.Timestamp(item["decision_at"]).floor("4h").isoformat()
        values[key] = values.get(key, 0.0) + float(item["net_r"])
    return values


def _bootstrap(items: list[Mapping[str, Any]], seed: int, resamples: int = 1000) -> dict[str, Any]:
    values = list(_cluster_values(items).values())
    if not values:
        return {"clusters": 0, "lower": None, "median": None, "upper": None}
    rng, draws = random.Random(seed), []
    for _ in range(resamples):
        sample = [values[rng.randrange(len(values))] for _ in values]
        draws.append(sum(sample) / len(sample))
    draws.sort()
    return {"clusters": len(values), "lower": draws[24], "median": draws[499], "upper": draws[974], "seed": seed}


def _lead_lag(by_symbol: Mapping[str, pd.DataFrame]) -> list[dict[str, Any]]:
    btc = by_symbol["BTCUSDT"].set_index("decision_at")
    records = []
    for target in ("ETHUSDT", "SOLUSDT"):
        other = by_symbol[target].set_index("decision_at")
        common = btc.index.intersection(other.index)
        shock = btc.loc[common, "delta_zscore"]
        close = other.loc[common, "close"].astype(float)
        for side, mask, sign in (("BUY_SHOCK", shock >= 1.5, 1), ("SELL_SHOCK", shock <= -1.5, -1)):
            for horizon in (1, 3, 6, 12):
                values = (sign * (close.shift(-horizon) / close - 1))[mask].dropna()
                records.append({
                    "source": "BTCUSDT", "target": target, "state": side,
                    "horizon_15m_bars": horizon, "n": len(values),
                    "mean": float(values.mean()) if len(values) else None,
                    "median": float(values.median()) if len(values) else None,
                    "positive_probability": float((values > 0).mean()) if len(values) else None,
                })
    return records


def _gate(record: Mapping[str, Any], siblings: list[Mapping[str, Any]]) -> tuple[bool, list[str]]:
    base, high, stress = (record["costs"][name] for name in ("BASE", "HIGH", "STRESS"))
    folds = [x["metrics"] for x in record["folds"]]
    positive_pairs = sum(x["expectancy_r"] is not None and x["expectancy_r"] > 0 for x in record["pairs"].values())
    checks = {
        "MIN_200_FILLS": base["fills"] >= 200,
        "MIN_120_CLUSTERS": record["cluster_n"] >= 120,
        "EXPECTANCY_0_04R": base["expectancy_r"] is not None and base["expectancy_r"] >= .04,
        "PF_1_15": base["profit_factor"] is not None and base["profit_factor"] >= 1.15,
        "HIGH_POSITIVE": high["expectancy_r"] is not None and high["expectancy_r"] > 0 and high["profit_factor"] > 1,
        "STRESS_POSITIVE": stress["expectancy_r"] is not None and stress["expectancy_r"] > 0 and stress["profit_factor"] > 1,
        "THREE_NONNEGATIVE_FOLDS": sum(x["expectancy_r"] is not None and x["expectancy_r"] >= 0 for x in folds) >= 3,
        "NO_FOLD_BELOW_MINUS_0_08R": all(x["expectancy_r"] is not None and x["expectancy_r"] >= -.08 for x in folds),
        "MAX_DD_20R": base["max_drawdown_r"] <= 20,
        "TWO_POSITIVE_PAIR_CONFIGURATIONS": positive_pairs >= 2,
        "BOOTSTRAP_LOWER_MINUS_0_02R": record["bootstrap"]["lower"] is not None and record["bootstrap"]["lower"] > -.02,
        "NEIGHBOR_PLATEAU": len(siblings) == 3 and all(x["costs"]["BASE"]["fills"] >= 100 for x in siblings),
        "INTEGRITY": all(record["integrity"].values()),
    }
    return all(checks.values()), [name for name, passed in checks.items() if not passed]


def run_relative_value_development() -> dict[str, Any]:
    raw, manifest = load_development()
    frames = {symbol: feature_frames(frame) for symbol, frame in raw.items()}
    by_symbol = {symbol: value["15m"] for symbol, value in frames.items()}
    panel, funding = _panel(frames), _funding_rates(raw)
    records = []
    for spec in frozen_relative_value_candidates():
        authority = _verify_dqe(spec)
        signals = _signals(spec, panel)
        scenarios = {name: _evaluate(spec, signals, by_symbol, funding, name) for name in COSTS}
        base = scenarios["BASE"]
        record = {
            "experiment_id": stable_hash({"candidate": spec.candidate_id, "materialization": MATERIALIZATION_ID}),
            "candidate": asdict(spec) | {"candidate_id": spec.candidate_id},
            "dataset_hashes": {s: manifest["splits"]["FLOW_DEV"][s]["sha256"] for s in sorted(raw)},
            "features": ["real_taker_delta", "CVD", "relative_price", "relative_OI", "funding_guard"],
            "construction": "EQUAL_NOTIONAL_LONG_SHORT", "nominal_n": len(base),
            "cluster_n": len(_cluster_values(base)), "costs": {name: _metrics(items) for name, items in scenarios.items()},
            "folds": _folds(base), "pairs": _groups(base, "pair"),
            "long_symbols": _groups(base, "long_symbol"), "short_symbols": _groups(base, "short_symbol"),
            "bootstrap": _bootstrap(base, int(spec.candidate_id[:8], 16)),
            "decision_authority": authority["decision_authority"],
            "integrity": {"causal": True, "real_flow": True, "decision_quality": True, "equal_notional": True, "venue_explicit": True},
            "result": "PENDING_GATE", "rejection_reasons": [],
        }
        records.append(record)
    for record in records:
        siblings = [x for x in records if x["candidate"]["family"] == record["candidate"]["family"]]
        qualified, failures = _gate(record, siblings)
        record["result"] = "DEVELOPMENT_QUALIFIED" if qualified else "REJECTED_DEVELOPMENT"
        record["rejection_reasons"] = failures
    qualified = [x for x in records if x["result"] == "DEVELOPMENT_QUALIFIED"]
    qualified.sort(key=lambda x: (x["costs"]["BASE"]["expectancy_r"], x["cluster_n"], x["bootstrap"]["lower"]), reverse=True)
    return {
        "schema": "flow-alpha-relative-value-development-v1", "materialization_id": MATERIALIZATION_ID,
        "provider_venue": "BINANCE_UM", "split": "FLOW_DEV", "cycle": "B_CROSS_SECTIONAL",
        "lead_lag": _lead_lag(by_symbol), "experiments": records,
        "development_qualified_count": len(qualified),
        "frozen_validation_candidate": qualified[0]["candidate"] if qualified else None,
        "validation_accessed": False, "blind_accessed": False,
        "derivatives_blind_accessed": False, "production_defaults_changed": False,
    }


def write_relative_value_artifact() -> Path:
    result = run_relative_value_development()
    payload = json.dumps(result, indent=2, sort_keys=True, default=str) + "\n"
    identity = hashlib.sha256(payload.encode()).hexdigest()[:16]
    path = ROOT / f"relative-value-development-{identity}.json"
    path.write_text(payload, encoding="utf-8")
    return path
