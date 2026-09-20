"""Independently validate and freeze the completed MASTER F1 artifact."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from services.baseline_edge_census import DatasetManifest, DatasetStore
from services.master_f1_confirmation import F1, frozen_confirmation_blocks


ROOT = Path("research_artifacts/master_f1_confirmation")
EXPECTED_COSTS = {
    "BASE_COST": {"fee_rate": .0005, "market_slippage_rate": .0003,
                  "funding_rate_per_bar": None, "source": "MODELED_DEFAULT"},
    "HIGHER_COST": {"fee_rate": .00075, "market_slippage_rate": .0005,
                    "funding_rate_per_bar": None, "source": "MODELED_HIGHER"},
    "STRESS_COST": {"fee_rate": .001, "market_slippage_rate": .001,
                    "funding_rate_per_bar": None, "source": "MODELED_STRESS"},
}


def _canonical_digest(value: Any, *, length: int | None = None) -> str:
    digest = hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    return digest[:length] if length else digest


def _file_digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def validate(path: Path) -> dict[str, Any]:
    artifact = json.loads(path.read_text(encoding="utf-8"))
    errors: list[str] = []
    expected_blocks = [block.as_dict() for block in frozen_confirmation_blocks()]
    if artifact.get("schema") != "master-f1-confirmation-result-v1":
        errors.append("RESULT_SCHEMA")
    if artifact.get("f1") != {"id": F1.candidate_id, "config_hash": F1.config_hash}:
        errors.append("F1_IDENTITY")
    if artifact.get("blocks") != expected_blocks:
        errors.append("BLOCK_BOUNDARIES")
    payload_digest = _canonical_digest(artifact, length=16)
    if path.stem != f"f1-confirmation-{payload_digest}":
        errors.append("ARTIFACT_NAME_DIGEST")

    datasets: dict[str, Any] = {}
    checkpoint_records: dict[str, Any] = {}
    evaluations = artifact.get("evaluations") or {}
    for symbol in ("BTC", "ETH", "SOL"):
        manifest_value = dict((artifact.get("datasets") or {}).get(symbol) or {})
        try:
            manifest = DatasetManifest(**manifest_value)
        except TypeError:
            errors.append(f"{symbol}_MANIFEST")
            continue
        csv_path = ROOT / f"{manifest.instrument}_{manifest.timeframe}_{manifest.dataset_id}.csv"
        try:
            frame = DatasetStore(ROOT).load(manifest)
            content_hash_valid = DatasetStore.content_hash(frame) == manifest.content_hash
        except Exception:
            content_hash_valid = False
        if not content_hash_valid or manifest.integrity_status != "VALID":
            errors.append(f"{symbol}_DATASET_INTEGRITY")
        datasets[symbol] = {
            "dataset_id": manifest.dataset_id, "content_hash": manifest.content_hash,
            "candle_count": manifest.candle_count, "start": manifest.start, "end": manifest.end,
            "content_hash_valid": content_hash_valid,
        }
        checkpoint = ROOT / f"base-evaluation-{symbol}-{manifest.dataset_id}-{F1.config_hash}.json"
        wrapper = json.loads(checkpoint.read_text(encoding="utf-8"))
        compatible = (
            wrapper.get("schema") == "master-f1-base-checkpoint-v1"
            and wrapper.get("f1_config_hash") == F1.config_hash
            and wrapper.get("dataset_id") == manifest.dataset_id
            and wrapper.get("blocks") == expected_blocks
        )
        evaluation_digest = _canonical_digest(wrapper.get("evaluation"))
        artifact_evaluation_digest = _canonical_digest(evaluations.get(symbol))
        if not compatible:
            errors.append(f"{symbol}_CHECKPOINT_COMPATIBILITY")
        if evaluation_digest != artifact_evaluation_digest:
            errors.append(f"{symbol}_CHECKPOINT_RECONCILIATION")
        evaluation = evaluations.get(symbol) or {}
        decisions = list(evaluation.get("decisions") or [])
        baseline = list(evaluation.get("baseline_outcomes") or [])
        f1_outcomes = list(evaluation.get("f1_outcomes") or [])
        if len(decisions) != 8106:
            errors.append(f"{symbol}_DECISION_COUNT")
        if len({row.get("decision_id") for row in decisions}) != len(decisions):
            errors.append(f"{symbol}_DUPLICATE_DECISION")
        block_map = {block["id"]: block for block in expected_blocks}
        for row in decisions:
            block = block_map.get(row.get("block_id"))
            value = str(row.get("decision_at"))
            if block is None or not (block["start"] <= value <= block["end"]):
                errors.append(f"{symbol}_DECISION_BOUNDARY")
                break
        baseline_approved = {row.get("decision_id") for row in decisions
                             if row.get("baseline_decision_outcome") == "APPROVED"}
        f1_approved = {row.get("f1_decision_id") for row in decisions
                       if row.get("f1_decision_outcome") == "APPROVED"}
        if any(row.get("signal_id") not in baseline_approved for row in baseline):
            errors.append(f"{symbol}_BASELINE_AUTHORITY")
        if any(row.get("signal_id") not in f1_approved for row in f1_outcomes):
            errors.append(f"{symbol}_F1_AUTHORITY")
        checkpoint_records[symbol] = {
            "file": str(checkpoint), "file_sha256": _file_digest(checkpoint),
            "compatible": compatible, "evaluation_digest": evaluation_digest,
            "artifact_evaluation_digest": artifact_evaluation_digest,
            "decision_count": len(decisions), "baseline_outcome_count": len(baseline),
            "f1_outcome_count": len(f1_outcomes),
        }

    scenarios = artifact.get("cost_scenarios") or {}
    for label, costs in EXPECTED_COSTS.items():
        if (scenarios.get(label) or {}).get("costs") != costs:
            errors.append(f"{label}_COSTS")
    parity = dict(artifact.get("base_cost_resimulation_parity") or {})
    if parity != {"BTC": True, "ETH": True, "SOL": True}:
        errors.append("CANONICAL_COST_REPLAY_PARITY")

    f1_metrics = dict((artifact.get("base") or {}).get("f1_metrics") or {})
    clusters = dict((artifact.get("base") or {}).get("f1_clusters") or {})
    blocks = dict((artifact.get("base") or {}).get("by_block") or {})
    block_metrics = {key: dict((value.get("f1") or {}).get("metrics") or {})
                     for key, value in blocks.items()}
    total_net_r = sum(float(metric.get("expectancy_r") or 0) * int(metric.get("trade_count") or 0)
                      for metric in block_metrics.values())
    catastrophic = [key for key, metric in block_metrics.items()
                    if int(metric.get("trade_count") or 0) >= 5
                    and (float(metric.get("expectancy_r") or 0) <= 0
                         or float(metric.get("profit_factor") or 0) <= .75)]
    positive_blocks = sum(int(metric.get("trade_count") or 0) >= 5
                          and float(metric.get("expectancy_r") or 0) > 0
                          for metric in block_metrics.values())
    dominant_block = max((float(metric.get("expectancy_r") or 0) * int(metric.get("trade_count") or 0)
                          / total_net_r if total_net_r > 0 else 1.0
                          for metric in block_metrics.values()), default=1.0)
    symbols = dict((artifact.get("base") or {}).get("f1_by_symbol") or {})
    symbol_fills = sum(int(metric.get("trade_count") or 0) for metric in symbols.values())
    symbol_concentration = max((int(metric.get("trade_count") or 0) / symbol_fills
                                for metric in symbols.values()), default=1.0)
    symbol_net_r = {symbol: float(metric.get("expectancy_r") or 0) * int(metric.get("trade_count") or 0)
                    for symbol, metric in symbols.items()}
    symbol_net_r_concentration = (max((value / total_net_r for value in symbol_net_r.values()), default=1.0)
                                  if total_net_r > 0 else 1.0)
    enough = int(f1_metrics.get("trade_count") or 0) >= 60 and int(clusters.get("fill_clusters") or 0) >= 30 and all(parity.values())
    checks = {
        "aggregate_expectancy": float(f1_metrics.get("expectancy_r") or 0) > 0,
        "aggregate_pf": float(f1_metrics.get("profit_factor") or 0) > 1.0,
        "no_catastrophic_block": not catastrophic,
        "higher_cost_expectancy": float((scenarios.get("HIGHER_COST") or {}).get("metrics", {}).get("expectancy_r") or 0) > 0,
        "positive_temporal_blocks": positive_blocks >= 2,
        "no_dominant_block": dominant_block <= .75,
        "symbol_diversification": symbol_concentration <= .75 and symbol_net_r_concentration <= .75,
        "cluster_diversification": (float(clusters.get("largest_cluster_net_r") or 0) <= .20 * total_net_r
                                    if total_net_r > 0 else False),
        "cost_block_floor": all(float(metric.get("profit_factor") or 0) > .75
                                for metric in (scenarios.get("HIGHER_COST") or {}).get("by_block", {}).values()
                                if int(metric.get("trade_count") or 0) >= 5),
    }
    if not enough:
        recomputed_status = "F1_INSUFFICIENT_NEW_EVIDENCE"
        recomputed_reasons = []
        if int(f1_metrics.get("trade_count") or 0) < 60 or int(clusters.get("fill_clusters") or 0) < 30:
            recomputed_reasons.append("PRE_REGISTERED_EFFECTIVE_SAMPLE_REQUIREMENT")
        if not all(parity.values()):
            recomputed_reasons.append("CANONICAL_COST_REPLAY_PARITY")
    else:
        recomputed_reasons = [name.upper() for name, passed in checks.items() if not passed]
        recomputed_status = "F1_NOT_CONFIRMED" if recomputed_reasons else "F1_CONFIRMED"
    if artifact.get("status") != recomputed_status or artifact.get("reasons") != recomputed_reasons:
        errors.append("CLASSIFICATION_RECONCILIATION")

    return {
        "schema": "master-f1-confirmation-validation-v1",
        "valid": not errors,
        "errors": errors,
        "source_artifact": str(path),
        "source_file_sha256": _file_digest(path),
        "source_payload_digest": payload_digest,
        "f1": artifact.get("f1"),
        "blocks": expected_blocks,
        "datasets": datasets,
        "checkpoints": checkpoint_records,
        "base_cost_resimulation_parity": parity,
        "counts": {"f1_fills": f1_metrics.get("trade_count"),
                   "fill_clusters": clusters.get("fill_clusters")},
        "qualification": {"enough_new_evidence": enough, "checks": checks,
                          "catastrophic_blocks": catastrophic, "positive_blocks": positive_blocks,
                          "aggregate_net_r": total_net_r, "recomputed_status": recomputed_status,
                          "recomputed_reasons": recomputed_reasons},
        "protected_partitions_accessed": [],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    report = validate(args.artifact)
    if args.write:
        target = ROOT / f"f1-confirmation-validation-{report['source_payload_digest']}.json"
        target.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        report["validation_artifact"] = str(target)
    print(json.dumps(report, indent=2, sort_keys=True))
    if not report["valid"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
