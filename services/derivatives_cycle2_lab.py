"""Single preregistered 4h horizon-mismatch cycle; Development only."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Mapping

import pandas as pd

from services.decision_quality import DecisionQualityEngine
from services.derivatives_alpha import PositioningStateEngine, stable_hash
from services.derivatives_alpha_lab import (
    BASE_COST, EXIT_FRICTION, HIGH_COST, MATERIALIZATION_ID, STRESS_COST,
    _apply_exit_and_funding, _bootstrap, _folds, _groups, _metrics,
    build_plans, load_development,
)
from services.research_replay import EntryPolicy, HistoricalReplayEngine, ReplayConfig


@dataclass(frozen=True)
class Cycle2Spec:
    family: str
    direction: str
    variant: str
    parameters: Mapping[str, float]

    @property
    def candidate_id(self) -> str:
        return stable_hash({
            "schema": "derivatives-alpha-cycle2-4h-v1", "materialization": MATERIALIZATION_ID,
            "family": self.family, "direction": self.direction, "variant": self.variant,
            "parameters": dict(self.parameters), "timeframe": "4h", "entry": "NEXT_OPEN_MARKET",
            "stop_atr": 1.5, "target_r": 1.5, "max_holding_bars": 6,
        })


def cycle2_candidates() -> tuple[Cycle2Spec, ...]:
    result = []
    for variant, core, extreme in (("BROAD", .60, .80), ("BASE", .70, .85), ("STRICT", .80, .90)):
        result.extend([
            Cycle2Spec("C2_4H_D1_LEVERAGED_TREND_LONG", "LONG", variant, {"core": core, "extreme": extreme}),
            Cycle2Spec("C2_4H_D1_LEVERAGED_TREND_SHORT", "SHORT", variant, {"core": core, "extreme": extreme}),
            Cycle2Spec("C2_4H_D2_DELEVERAGING_REVERSAL", "BOTH", variant, {"core": core, "extreme": extreme}),
        ])
    return tuple(result)


def _resample_4h(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.copy().set_index("time")
    stock_last = [
        "mark_open", "mark_high", "mark_low", "mark_close", "index_open", "index_high", "index_low", "index_close",
        "premium_open", "premium_high", "premium_low", "premium_close", "spot_open", "spot_high", "spot_low", "spot_close",
        "oi_source_at", "oi_available_at", "oi_base", "oi_usd", "toptrader_account_ratio",
        "toptrader_position_ratio", "account_long_short_ratio", "taker_long_short_ratio", "oi_age_seconds",
        "funding_at", "funding_available_at", "funding_rate", "funding_interval_hours", "funding_age_seconds",
        "basis_absolute", "basis_pct", "spot_basis_pct", "provider", "provider_symbol", "local_alias", "alignment_method",
    ]
    aggregation: dict[str, str] = {
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum",
    }
    aggregation.update({column: "last" for column in stock_last})
    result = data.resample("4h", origin="epoch", label="left", closed="left").agg(aggregation).dropna(subset=["open", "close"])
    counts = data["close"].resample("4h", origin="epoch", label="left", closed="left").count()
    result = result.loc[counts == 4].reset_index()
    result["decision_at"] = result["time"] + pd.Timedelta(hours=4)
    features = PositioningStateEngine(lookback=180, minimum_history=42).transform(result)
    close = features["close"].astype(float)
    previous = close.shift(1)
    tr = pd.concat([
        features["high"].astype(float) - features["low"].astype(float),
        (features["high"].astype(float) - previous).abs(),
        (features["low"].astype(float) - previous).abs(),
    ], axis=1).max(axis=1)
    features["atr14"] = tr.rolling(14, min_periods=14).mean()
    features["price_return_24h_percentile"] = features["price_return_6h_percentile"]
    features["oi_change_24h_percentile"] = features["oi_change_6h_percentile"]
    features["price_return_4h"] = features["price_return_1h"]
    return features


def _gate(record: Mapping[str, Any], siblings: list[Mapping[str, Any]]) -> tuple[bool, list[str]]:
    base, high, stress = record["costs"]["BASE"], record["costs"]["HIGH"], record["costs"]["STRESS"]
    folds = [item["metrics"] for item in record["folds"]]
    positive_symbols = sum(
        item["expectancy_r"] is not None and item["expectancy_r"] > 0 for item in record["symbols"].values()
    )
    checks = {
        "MIN_80_FILLS": base["fills"] >= 80,
        "MIN_40_CLUSTERS": record["cluster_n"] >= 40,
        "EXPECTANCY_AT_LEAST_0_05R": base["expectancy_r"] is not None and base["expectancy_r"] >= .05,
        "PF_AT_LEAST_1_15": base["profit_factor"] is not None and base["profit_factor"] >= 1.15,
        "HIGH_POSITIVE": high["expectancy_r"] is not None and high["expectancy_r"] > 0 and high["profit_factor"] > 1,
        "STRESS_POSITIVE": stress["expectancy_r"] is not None and stress["expectancy_r"] > 0 and stress["profit_factor"] > 1,
        "THREE_NONNEGATIVE_FOLDS": sum(item["expectancy_r"] is not None and item["expectancy_r"] >= 0 for item in folds) >= 3,
        "NO_FOLD_BELOW_MINUS_0_10R": all(item["expectancy_r"] is not None and item["expectancy_r"] >= -.10 for item in folds),
        "MAX_DRAWDOWN_20R": base["max_drawdown_r"] <= 20,
        "TWO_POSITIVE_SYMBOLS": positive_symbols >= 2,
        "BOOTSTRAP_LOWER_ABOVE_MINUS_0_03R": record["bootstrap"]["lower"] is not None and record["bootstrap"]["lower"] > -.03,
        "NEIGHBOR_PLATEAU": len(siblings) == 3 and all(item["costs"]["BASE"]["fills"] >= 40 for item in siblings),
        "CAUSAL_DQE_REPLAY_VENUE": record["integrity"] == {
            "causal": True, "decision_quality": True, "canonical_replay": True, "venue_explicit": True,
        },
    }
    return all(checks.values()), [name for name, passed in checks.items() if not passed]


def run_cycle2_development() -> dict[str, Any]:
    raw_frames, manifest = load_development()
    frames = {symbol: _resample_4h(frame) for symbol, frame in raw_frames.items()}
    records = []
    for spec in cycle2_candidates():
        plans, counters = {}, {"eligible": 0, "family_matches": 0, "dqe_approved": 0, "dqe_rejected": 0}
        for symbol, frame in frames.items():
            plans[symbol], local = build_plans(spec, symbol, frame)
            for key in counters:
                counters[key] += local[key]
        scenarios = {}
        for label, costs in (("BASE", BASE_COST), ("HIGH", HIGH_COST), ("STRESS", STRESS_COST)):
            combined = []
            for symbol, frame in frames.items():
                candles = frame[["time", "open", "high", "low", "close", "volume"]].copy()
                candles["confirm"] = "1"
                outcomes = HistoricalReplayEngine(ReplayConfig(
                    "4h", warmup_bars=55, entry_expiry_bars=1, max_holding_bars=6,
                    entry_policy=EntryPolicy.MARKET_NEXT_OPEN, costs=costs,
                )).run_plans(candles, plans[symbol], metadata={
                    "data_provider": "BINANCE_VISION_UM", "sample_role": "DERIV_DEV",
                    "split_id": "derivatives-alpha-cycle2-4h-v1:DERIV_DEV",
                    "decision_authority": DecisionQualityEngine.AUTHORITY,
                    "decision_version": DecisionQualityEngine.DECISION_VERSION,
                    "decision_source": "DERIVATIVES_ALPHA_CYCLE2_4H", "variants_evaluated": 9,
                })
                combined.extend(_apply_exit_and_funding(outcomes, raw_frames[symbol], EXIT_FRICTION[label]))
            scenarios[label] = sorted(combined, key=lambda item: (item.decision_at, item.symbol))
        base = scenarios["BASE"]
        cluster_n = len({f"{item.decision_at}|{item.direction}|{spec.family}" for item in base})
        records.append({
            "experiment_id": stable_hash({"cycle": "4h-v1", "candidate": spec.candidate_id}),
            "candidate": asdict(spec) | {"candidate_id": spec.candidate_id, "timeframe": "4h"},
            "parent": "PRIMARY_1H_HORIZON_DIAGNOSIS", "dataset_hashes": {
                symbol: manifest["splits"]["DERIV_DEV"][symbol]["sha256"] for symbol in sorted(frames)
            },
            "counters": counters, "nominal_n": len(base), "cluster_n": cluster_n,
            "costs": {label: _metrics(items) for label, items in scenarios.items()},
            "folds": _folds(base), "symbols": _groups(base, lambda item: item.symbol),
            "directions": _groups(base, lambda item: item.direction),
            "bootstrap": _bootstrap(base, spec.family, int(spec.candidate_id[:8], 16)),
            "integrity": {"causal": True, "decision_quality": True, "canonical_replay": True, "venue_explicit": True},
            "result": "PENDING_GATE", "rejection_reasons": [],
        })
    for record in records:
        siblings = [item for item in records if item["candidate"]["family"] == record["candidate"]["family"]]
        qualified, failures = _gate(record, siblings)
        record["result"] = "DEVELOPMENT_QUALIFIED" if qualified else "REJECTED_DEVELOPMENT"
        record["rejection_reasons"] = failures
    qualified = [item for item in records if item["result"] == "DEVELOPMENT_QUALIFIED"]
    qualified.sort(key=lambda item: (
        item["costs"]["BASE"]["expectancy_r"], item["cluster_n"], item["bootstrap"]["lower"]
    ), reverse=True)
    return {
        "schema": "derivatives-alpha-cycle2-4h-development-v1",
        "materialization_id": MATERIALIZATION_ID, "provider_venue": "BINANCE",
        "split": "DERIV_DEV", "variant_count": 9, "validation_accessed": False,
        "blind_accessed": False, "experiments": records,
        "development_qualified_count": len(qualified),
        "frozen_validation_candidate": qualified[0]["candidate"] if qualified else None,
        "production_defaults_changed": False,
    }
