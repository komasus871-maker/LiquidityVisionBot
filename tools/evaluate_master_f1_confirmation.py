"""Evaluate the preregistered F1 confirmation blocks from local immutable data."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

from services.baseline_edge_census import DatasetManifest, DatasetStore
from services.master_f1_confirmation import (
    F1, F1ConfirmationRunner, block_evaluations, frozen_confirmation_blocks,
    replay_frozen_f1_cost_scenario,
)
from services.phase3b_edge_surgery import _outcome_from_value, block_bootstrap_expectancy_r
from services.decision_quality import DecisionQualityEngine
from services.research_replay import PerformanceAttribution, ReplayCostModel, ReplayOutcome


ROOT = Path("research_artifacts/master_f1_confirmation")
COSTS = {
    "BASE_COST": ReplayCostModel(fee_rate=0.0005, market_slippage_rate=0.0003, source="MODELED_DEFAULT"),
    "HIGHER_COST": ReplayCostModel(fee_rate=0.00075, market_slippage_rate=0.0005, source="MODELED_HIGHER"),
    "STRESS_COST": ReplayCostModel(fee_rate=0.0010, market_slippage_rate=0.0010, source="MODELED_STRESS"),
}


def _load(symbol: str) -> tuple[DatasetManifest, Any]:
    for path in ROOT.glob("*.manifest.json"):
        value = json.loads(path.read_text(encoding="utf-8"))
        if value["instrument"] == symbol:
            manifest = DatasetManifest(**value)
            return manifest, DatasetStore(ROOT).load(manifest)
    raise RuntimeError(f"missing immutable manifest for {symbol}")


def _clusters(outcomes: Iterable[ReplayOutcome]) -> dict[str, Any]:
    groups: dict[tuple[str, str], list[ReplayOutcome]] = {}
    for item in outcomes:
        if item.simulated_fill is not None:
            groups.setdefault((item.decision_at, item.direction), []).append(item)
    clusters = list(groups.values())
    correlated = [group for group in clusters if len(group) > 1]
    return {"fill_clusters": len(clusters), "correlated_clusters": len(correlated),
            "fills_in_correlated_clusters": sum(len(group) for group in correlated),
            "max_cluster_size": max((len(group) for group in clusters), default=0),
            "largest_cluster_net_r": round(max((sum(item.net_r for item in group) for group in clusters), default=0.0), 12),
            "aggregate_net_r": round(sum(item.net_r for group in clusters for item in group), 12)}


def _metrics(outcomes: Iterable[ReplayOutcome]) -> dict[str, Any]:
    items = list(outcomes)
    metrics = PerformanceAttribution.metrics(items)
    executed = [item for item in items if item.simulated_fill is not None]
    durations = [int(item.provenance.get("bars_held")) for item in executed
                 if item.provenance.get("bars_held") is not None]
    metrics["average_holding_bars"] = round(sum(durations) / len(durations), 12) if durations else None
    return metrics


def _by_symbol(outcomes: Iterable[ReplayOutcome]) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[ReplayOutcome]] = {}
    for item in outcomes:
        groups.setdefault(item.symbol, []).append(item)
    return {symbol: _metrics(items) for symbol, items in sorted(groups.items())}


def _by_block(outcomes: Iterable[ReplayOutcome], evaluation: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = {str(item["f1_decision_id"]): item["block_id"] for item in evaluation["decisions"]}
    groups: dict[str, list[ReplayOutcome]] = {}
    for item in outcomes:
        groups.setdefault(rows[item.signal_id], []).append(item)
    return {block: _metrics(items) for block, items in sorted(groups.items())}


def _outcome_signature(item: ReplayOutcome) -> tuple[Any, ...]:
    return (item.signal_id, item.simulated_fill, item.exit_price, item.exit_reason, item.gross_pnl,
            item.entry_fee, item.exit_fee, item.net_pnl, item.net_r, item.fill_at, item.exit_at)


def _serialize(value: Any) -> Any:
    if isinstance(value, ReplayOutcome):
        return value.as_dict()
    if isinstance(value, ReplayCostModel):
        return asdict(value)
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(item) for item in value]
    return value


def _checkpoint_path(symbol: str, manifest: DatasetManifest) -> Path:
    return ROOT / f"base-evaluation-{symbol}-{manifest.dataset_id}-{F1.config_hash}.json"


def _restore_evaluation(value: dict[str, Any]) -> dict[str, Any]:
    value["baseline_outcomes"] = [_outcome_from_value(item) for item in value["baseline_outcomes"]]
    value["f1_outcomes"] = [_outcome_from_value(item) for item in value["f1_outcomes"]]
    return value


def _evaluate_or_resume(
    symbol: str, *, runner: F1ConfirmationRunner, frame: Any, manifest: DatasetManifest,
    blocks: Any, anchor_frame: Any,
) -> dict[str, Any]:
    checkpoint = _checkpoint_path(symbol, manifest)
    if checkpoint.exists():
        wrapper = json.loads(checkpoint.read_text(encoding="utf-8"))
        if (wrapper.get("schema") != "master-f1-base-checkpoint-v1"
                or wrapper.get("f1_config_hash") != F1.config_hash
                or wrapper.get("dataset_id") != manifest.dataset_id
                or wrapper.get("blocks") != [block.as_dict() for block in blocks]):
            raise RuntimeError(f"stale or incompatible confirmation checkpoint: {checkpoint}")
        return _restore_evaluation(dict(wrapper["evaluation"]))
    evaluation = runner.evaluate(
        frame, manifest, blocks=blocks, anchor_frame=anchor_frame, costs=COSTS["BASE_COST"],
    )
    wrapper = {"schema": "master-f1-base-checkpoint-v1", "f1_config_hash": F1.config_hash,
               "dataset_id": manifest.dataset_id, "blocks": [block.as_dict() for block in blocks],
               "evaluation": evaluation}
    checkpoint.write_text(json.dumps(_serialize(wrapper), indent=2, sort_keys=True, default=str), encoding="utf-8")
    return evaluation


def main() -> None:
    manifests, frames = {}, {}
    for symbol in ("BTC", "ETH", "SOL"):
        manifests[symbol], frames[symbol] = _load(symbol)
    blocks = frozen_confirmation_blocks()
    runner = F1ConfirmationRunner()
    evaluations: dict[str, dict[str, Any]] = {}
    for symbol in ("BTC", "ETH", "SOL"):
        evaluations[symbol] = _evaluate_or_resume(
            symbol, runner=runner, frame=frames[symbol], manifest=manifests[symbol], blocks=blocks,
            anchor_frame=None if symbol == "BTC" else frames["BTC"],
        )

    base_f1 = [item for evaluation in evaluations.values() for item in evaluation["f1_outcomes"]]
    base_control = [item for evaluation in evaluations.values() for item in evaluation["baseline_outcomes"]]
    cost_scenarios: dict[str, dict[str, Any]] = {}
    parity: dict[str, bool] = {}
    for label, costs in COSTS.items():
        combined: list[ReplayOutcome] = []
        by_symbol: dict[str, list[ReplayOutcome]] = {}
        for symbol, evaluation in evaluations.items():
            replayed = replay_frozen_f1_cost_scenario(evaluation, frames[symbol], costs=costs)
            by_symbol[symbol] = replayed
            if label == "BASE_COST":
                parity[symbol] = sorted(_outcome_signature(item) for item in replayed) == sorted(
                    _outcome_signature(item) for item in evaluation["f1_outcomes"]
                )
            combined.extend(replayed)
        decision_blocks = {symbol: {row["f1_decision_id"]: row["block_id"]
                                    for row in evaluation["decisions"]}
                           for symbol, evaluation in evaluations.items()}
        cost_scenarios[label] = {"costs": costs, "metrics": _metrics(combined),
                                 "by_block": {block.block_id: _metrics([
                                     item for symbol, items in by_symbol.items() for item in items
                                     if decision_blocks[symbol][item.signal_id] == block.block_id
                                 ]) for block in blocks}}

    base_by_block: dict[str, dict[str, Any]] = {}
    for block in blocks:
        baseline, f1_items = [], []
        for evaluation in evaluations.values():
            for row in block_evaluations(evaluation):
                if row["block"]["id"] == block.block_id:
                    baseline.extend(row["baseline"]["outcomes"])
                    f1_items.extend(row["f1"]["outcomes"])
        base_by_block[block.block_id] = {
            "decision_count": sum(1 for evaluation in evaluations.values()
                                  for row in evaluation["decisions"] if row["block_id"] == block.block_id),
            "baseline_approved": sum(1 for evaluation in evaluations.values()
                                     for row in evaluation["decisions"] if row["block_id"] == block.block_id
                                     and row["baseline_decision_outcome"] == DecisionQualityEngine.APPROVED),
            "f1_approved": sum(1 for evaluation in evaluations.values()
                               for row in evaluation["decisions"] if row["block_id"] == block.block_id
                               and row["f1_decision_outcome"] == DecisionQualityEngine.APPROVED),
            "baseline": {"metrics": _metrics(baseline),
                         "uncertainty": block_bootstrap_expectancy_r(baseline)},
            "f1": {"metrics": _metrics(f1_items),
                   "uncertainty": block_bootstrap_expectancy_r(f1_items)},
        }
    f1_metrics = _metrics(base_f1)
    f1_symbols = _by_symbol(base_f1)
    f1_clusters = _clusters(base_f1)
    block_metrics = {key: value["f1"]["metrics"] for key, value in base_by_block.items()}
    total_net_r = sum(metric["expectancy_r"] * metric["trade_count"] for metric in block_metrics.values())
    catastrophic = [key for key, metric in block_metrics.items()
                    if metric["trade_count"] >= 5 and (metric["expectancy_r"] <= 0 or (metric["profit_factor"] or 0) <= .75)]
    positive_blocks = sum(metric["trade_count"] >= 5 and metric["expectancy_r"] > 0
                          for metric in block_metrics.values())
    dominant_block = max(((metric["expectancy_r"] * metric["trade_count"] / total_net_r if total_net_r > 0 else 1.0)
                          for metric in block_metrics.values()), default=1.0)
    symbol_fills = sum(metric["trade_count"] for metric in f1_symbols.values())
    symbol_concentration = max((metric["trade_count"] / symbol_fills for metric in f1_symbols.values()), default=1.0)
    symbol_net_r = {symbol: metric["expectancy_r"] * metric["trade_count"]
                    for symbol, metric in f1_symbols.items()}
    symbol_net_r_concentration = max((value / total_net_r for value in symbol_net_r.values()), default=1.0) if total_net_r > 0 else 1.0
    status = "F1_CONFIRMED"
    reasons = []
    if (f1_metrics["trade_count"] < 60 or f1_clusters["fill_clusters"] < 30
            or not all(parity.values())):
        reasons = []
        if f1_metrics["trade_count"] < 60 or f1_clusters["fill_clusters"] < 30:
            reasons.append("PRE_REGISTERED_EFFECTIVE_SAMPLE_REQUIREMENT")
        if not all(parity.values()):
            reasons.append("CANONICAL_COST_REPLAY_PARITY")
        status = "F1_INSUFFICIENT_NEW_EVIDENCE"
    else:
        checks = {
            "aggregate_expectancy": f1_metrics["expectancy_r"] > 0,
            "aggregate_pf": (f1_metrics["profit_factor"] or 0) > 1.0,
            "no_catastrophic_block": not catastrophic,
            "higher_cost_expectancy": cost_scenarios["HIGHER_COST"]["metrics"]["expectancy_r"] > 0,
            "positive_temporal_blocks": positive_blocks >= 2,
            "no_dominant_block": dominant_block <= .75,
            "symbol_diversification": symbol_concentration <= .75 and symbol_net_r_concentration <= .75,
            "cluster_diversification": f1_clusters["largest_cluster_net_r"] <= .20 * total_net_r if total_net_r > 0 else False,
            "cost_block_floor": all((metrics["profit_factor"] or 0) > .75
                                    for metrics in cost_scenarios["HIGHER_COST"]["by_block"].values()
                                    if metrics["trade_count"] >= 5),
        }
        reasons = [name.upper() for name, passed in checks.items() if not passed]
        if reasons:
            status = "F1_NOT_CONFIRMED"
    payload = {
        "schema": "master-f1-confirmation-result-v1", "f1": {"id": F1.candidate_id, "config_hash": F1.config_hash},
        "status": status, "reasons": reasons, "blocks": [block.as_dict() for block in blocks],
        "datasets": {symbol: manifests[symbol].as_dict() for symbol in manifests},
        "base": {"baseline_metrics": _metrics(base_control), "f1_metrics": f1_metrics,
                 "baseline_uncertainty": block_bootstrap_expectancy_r(base_control),
                 "f1_uncertainty": block_bootstrap_expectancy_r(base_f1),
                 "by_block": base_by_block, "f1_by_symbol": f1_symbols, "f1_clusters": f1_clusters},
        "cost_scenarios": cost_scenarios, "base_cost_resimulation_parity": parity,
        "qualification_inputs": {"catastrophic_blocks": catastrophic, "positive_blocks": positive_blocks,
                                  "dominant_block_net_r_share": dominant_block,
                                  "largest_symbol_fill_share": symbol_concentration,
                                  "largest_symbol_net_r_share": symbol_net_r_concentration},
        "evaluations": evaluations,
    }
    serial = _serialize(payload)
    digest = hashlib.sha256(json.dumps(serial, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()[:16]
    target = ROOT / f"f1-confirmation-{digest}.json"
    target.write_text(json.dumps(serial, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print(json.dumps({"status": status, "reasons": reasons, "artifact": str(target),
                      "base_f1_metrics": f1_metrics, "base_control_metrics": _metrics(base_control),
                      "costs": {key: value["metrics"] for key, value in cost_scenarios.items()},
                      "parity": parity}, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
