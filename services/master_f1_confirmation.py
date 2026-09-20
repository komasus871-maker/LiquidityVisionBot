"""Independent, read-only confirmation replay for frozen Phase 3C F1.

This module is deliberately research-only.  It runs the existing Analyzer and
DecisionQualityEngine over a separately materialized earlier dataset, then
applies the frozen F1 family admission rule.  It has no runtime import path,
does not contain Phase 3B protected-window timestamps, and cannot place orders.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping

import pandas as pd

from services.analyzer import Analyzer
from services.baseline_edge_census import DatasetManifest, _ResearchMemory
from services.decision_quality import DecisionQualityEngine
from services.market_context import MarketContextEngine
from services.phase3b_edge_surgery import PARENT_BASELINE_CONFIG, analyzer_attribution, block_bootstrap_expectancy_r
from services.phase3c_edge_decomposition import FamilyCandidate, classify_family
from services.research_features import attach_causal_feature_cache
from services.research_replay import (
    EntryPolicy, HistoricalReplayEngine, PerformanceAttribution, ReplayConfig,
    ReplayCostModel, ReplayOutcome, _as_utc,
)
from utils.timeframe import timeframe_seconds


MASTER_F1_SCHEMA_VERSION = "master-f1-confirmation-v1"
FROZEN_F1_CONFIG_HASH = "c49803034d94792e"
F1 = FamilyCandidate(
    "F1_COUNTERTREND_REVERSAL_LONG",
    "COUNTERTREND_REVERSAL_LONG",
    "Existing Trend conflicts plus CHOCH confirmation, LONG direction",
)
if F1.config_hash != FROZEN_F1_CONFIG_HASH:  # Immutable Phase 3C identity guard.
    raise RuntimeError("master confirmation F1 identity does not match the frozen Phase 3C candidate")


@dataclass(frozen=True)
class ConfirmationBlock:
    block_id: str
    start: datetime
    end: datetime
    access_end: datetime

    def includes(self, value: datetime) -> bool:
        return self.start <= _as_utc(value) <= self.end

    def as_dict(self) -> dict[str, str]:
        return {"id": self.block_id, "start": self.start.isoformat(), "end": self.end.isoformat(),
                "access_end": self.access_end.isoformat()}


def frozen_confirmation_blocks() -> tuple[ConfirmationBlock, ...]:
    return (
        ConfirmationBlock("CONFIRM_A", datetime.fromisoformat("2024-09-14T16:00:00+00:00"),
                          datetime.fromisoformat("2025-01-05T05:00:00+00:00"),
                          datetime.fromisoformat("2025-01-11T05:00:00+00:00")),
        ConfirmationBlock("CONFIRM_B", datetime.fromisoformat("2025-01-11T06:00:00+00:00"),
                          datetime.fromisoformat("2025-05-03T19:00:00+00:00"),
                          datetime.fromisoformat("2025-05-09T19:00:00+00:00")),
        ConfirmationBlock("CONFIRM_C", datetime.fromisoformat("2025-05-09T20:00:00+00:00"),
                          datetime.fromisoformat("2025-08-30T09:00:00+00:00"),
                          datetime.fromisoformat("2025-09-05T09:00:00+00:00")),
    )


def _block_for(value: datetime, blocks: Iterable[ConfirmationBlock]) -> ConfirmationBlock | None:
    return next((block for block in blocks if block.includes(value)), None)


def _plan(record: Mapping[str, Any], *, strategy_version: str) -> dict[str, Any]:
    return {
        "signal_id": str(record["decision_id"]), "strategy_version": strategy_version,
        "symbol": str(record["symbol"]), "direction": str(record["direction"]),
        "entry": record["entry"], "stop": record["stop"], "tp1": record["tp1"],
        "entry_policy": (EntryPolicy.MARKET_NEXT_OPEN.value
                         if record.get("entry_type") == "MARKET_READY"
                         else EntryPolicy.LIMIT_AFTER_DECISION.value),
        "metadata": dict(record),
    }


def _replay_recorded_plans(
    frame: pd.DataFrame, plans: Mapping[str, Mapping[str, Any]], *, config: ReplayConfig,
    metadata: Mapping[str, Any],
) -> list[ReplayOutcome]:
    """Re-run frozen decision-time plans under a specified execution-cost model."""
    replay = HistoricalReplayEngine(config)
    if not timeframe_seconds(config.timeframe):
        raise ValueError("confirmation requires a canonical timeframe")
    return replay.run_plans(frame, plans, metadata=metadata)


class F1ConfirmationRunner:
    """Runs the unchanged decision authority once, then replays baseline/F1 plans."""

    def __init__(self, *, replay_config: ReplayConfig | None = None):
        self.replay_config = replay_config or ReplayConfig(timeframe="1h")

    def evaluate(
        self, frame: pd.DataFrame, manifest: DatasetManifest, *, blocks: Iterable[ConfirmationBlock],
        anchor_frame: pd.DataFrame | None = None, costs: ReplayCostModel | None = None,
    ) -> dict[str, Any]:
        blocks = tuple(blocks)
        if not blocks:
            raise ValueError("at least one pre-registered confirmation block is required")
        end = max(block.access_end for block in blocks)
        available = frame.loc[pd.to_datetime(frame["time"], utc=True) <= pd.Timestamp(end)].copy()
        config = ReplayConfig(**{**self.replay_config.__dict__, "costs": costs or self.replay_config.costs})
        replay = HistoricalReplayEngine(config)
        prepared = attach_causal_feature_cache(replay.prepare(available))
        anchor = attach_causal_feature_cache(replay.prepare(anchor_frame.loc[
            pd.to_datetime(anchor_frame["time"], utc=True) <= pd.Timestamp(end)
        ].copy())) if anchor_frame is not None else None
        analyzer, quality, context = Analyzer(), DecisionQualityEngine(), MarketContextEngine()
        quality.memory = _ResearchMemory()
        seconds = timeframe_seconds(manifest.timeframe)
        if not seconds:
            raise ValueError("confirmation requires a canonical timeframe")

        decisions: list[dict[str, Any]] = []
        baseline_plans: dict[str, dict[str, Any]] = {}
        f1_plans: dict[str, dict[str, Any]] = {}
        for index in range(config.warmup_bars - 1, len(prepared) - 1):
            snapshot = prepared.iloc[: index + 1]
            decision_at = _as_utc(snapshot.iloc[-1]["time"] + pd.Timedelta(seconds=seconds))
            block = _block_for(decision_at, blocks)
            if block is None:
                continue
            analysis = analyzer.analyze(snapshot, symbol=manifest.instrument, timeframe=manifest.timeframe,
                                        source="MASTER_F1_CONFIRMATION", use_cache=False,
                                        research_envelope=False, prevalidated_research=True)
            if anchor is not None and manifest.instrument != "BTC":
                anchor_snapshot = anchor.loc[anchor["time"] <= snapshot.iloc[-1]["time"]]
                if len(anchor_snapshot) >= config.warmup_bars:
                    btc = analyzer.analyze(anchor_snapshot, symbol="BTC", timeframe=manifest.timeframe,
                                           source="MASTER_F1_CONFIRMATION_BTC_CONTEXT", use_cache=False,
                                           research_envelope=False, prevalidated_research=True)
                    analysis = context.enrich(analysis, symbol=manifest.instrument, btc=btc)
            analysis.update({"historical_probability": {"samples": 0, "sufficient": False},
                             "similar_cases": [], "similar_stats": {"samples": 0},
                             "weighted_similarity": {"samples": 0, "estimated": False},
                             "historical_intelligence": {"samples": 0, "estimated": False,
                                                         "reliability": "Unavailable"},
                             "timestamp": decision_at.isoformat(), "symbol": manifest.instrument,
                             "timeframe": manifest.timeframe})
            analysis = quality.enrich(analysis, source="MASTER_F1_CONFIRMATION")
            baseline_id = f"BASELINE:{manifest.instrument}:{manifest.timeframe}:{decision_at.isoformat()}"
            f1_id = baseline_id.replace("BASELINE:", f"{F1.candidate_id}:", 1)
            approved = str(analysis.get("decision_outcome")) == DecisionQualityEngine.APPROVED
            family = classify_family({"direction": analysis.get("direction"),
                                      "score_components": analysis.get("score_components") or []})
            f1_approved = approved and family == F1.family
            record = {
                "decision_id": baseline_id, "f1_decision_id": f1_id, "symbol": manifest.instrument,
                "timeframe": manifest.timeframe, "decision_at": decision_at.isoformat(),
                "block_id": block.block_id, "baseline_decision_outcome": analysis.get("decision_outcome"),
                "f1_decision_outcome": DecisionQualityEngine.APPROVED if f1_approved else "NO_TRADE",
                "f1_rejection": None if f1_approved else "FROZEN_F1_FAMILY_NOT_ADMITTED",
                "direction": analysis.get("direction"), "confidence": analysis.get("confidence"),
                "probability": analysis.get("probability"), "quality_score": analysis.get("setup_score"),
                "entry": analysis.get("entry"), "stop": analysis.get("stop"), "tp1": analysis.get("tp1"),
                "rr": analysis.get("rr"), "entry_type": analysis.get("entry_type"),
                "decision_veto_reasons": list(analysis.get("decision_veto_reasons") or []),
                "attribution": analyzer_attribution(analysis), "phase3c_family": family,
                "dataset_id": manifest.dataset_id, "decision_authority": analysis.get("decision_authority"),
                "decision_version": analysis.get("decision_version"),
            }
            decisions.append(record)
            if approved:
                baseline_plans[decision_at.isoformat()] = _plan(record, strategy_version=PARENT_BASELINE_CONFIG)
            if f1_approved:
                f1_record = {**record, "decision_id": f1_id, "candidate_id": F1.candidate_id,
                             "candidate_config_hash": F1.config_hash}
                f1_plans[decision_at.isoformat()] = _plan(f1_record, strategy_version=F1.config_hash)

        common = {"data_provider": manifest.provider, "decision_authority": DecisionQualityEngine.AUTHORITY,
                  "decision_version": DecisionQualityEngine.DECISION_VERSION, "variants_evaluated": 1,
                  "sample_role": "MASTER_F1_CONFIRMATION", "split_id": "PRE_REGISTERED_CONFIRMATION",
                  "evaluation_window": f"{blocks[0].start.isoformat()}/{blocks[-1].end.isoformat()}"}
        baseline_outcomes = _replay_recorded_plans(
            prepared, baseline_plans, config=config,
            metadata={**common, "decision_source": "MASTER_F1_CONFIRMATION_BASELINE"},
        )
        f1_outcomes = _replay_recorded_plans(
            prepared, f1_plans, config=config,
            metadata={**common, "decision_source": "MASTER_F1_CONFIRMATION_F1"},
        )
        return {"schema": MASTER_F1_SCHEMA_VERSION, "dataset": manifest.as_dict(),
                "blocks": [block.as_dict() for block in blocks], "costs": config.costs,
                "frozen_parent_config": PARENT_BASELINE_CONFIG,
                "f1": {"id": F1.candidate_id, "family": F1.family, "config_hash": F1.config_hash},
                "decisions": decisions, "baseline_outcomes": baseline_outcomes, "f1_outcomes": f1_outcomes}


def block_evaluations(evaluation: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Split one canonical confirmation pass into non-overlapping declared blocks."""
    rows = list(evaluation["decisions"])
    baseline = list(evaluation["baseline_outcomes"])
    f1 = list(evaluation["f1_outcomes"])
    record_by_baseline = {str(row["decision_id"]): row for row in rows}
    record_by_f1 = {str(row["f1_decision_id"]): row for row in rows}
    result = []
    for block in evaluation["blocks"]:
        block_id = block["id"]
        block_rows = [row for row in rows if row["block_id"] == block_id]
        base = [item for item in baseline if record_by_baseline[item.signal_id]["block_id"] == block_id]
        f1_items = [item for item in f1 if record_by_f1[item.signal_id]["block_id"] == block_id]
        result.append({"block": block, "decisions": block_rows,
                       "baseline": {"outcomes": base, "metrics": PerformanceAttribution.metrics(base),
                                    "uncertainty": block_bootstrap_expectancy_r(base)},
                       "f1": {"outcomes": f1_items, "metrics": PerformanceAttribution.metrics(f1_items),
                              "uncertainty": block_bootstrap_expectancy_r(f1_items)}})
    return result


def replay_frozen_f1_cost_scenario(
    evaluation: Mapping[str, Any], frame: pd.DataFrame, *, costs: ReplayCostModel,
) -> list[ReplayOutcome]:
    """Reprice only the previously approved frozen F1 plans with canonical replay.

    Decision-time analysis is intentionally not repeated: this takes the exact
    recorded F1-approved plans from a base confirmation pass and changes only
    the outcome-independent cost configuration.  The replay still recomputes
    fills, exits and fees from candles, so market-next-open slippage is not
    approximated after the fact.
    """
    blocks = tuple(ConfirmationBlock(
        str(item["id"]), datetime.fromisoformat(str(item["start"])),
        datetime.fromisoformat(str(item["end"])), datetime.fromisoformat(str(item["access_end"])),
    ) for item in evaluation["blocks"])
    end = max(item.access_end for item in blocks)
    available = frame.loc[pd.to_datetime(frame["time"], utc=True) <= pd.Timestamp(end)].copy()
    plans: dict[str, dict[str, Any]] = {}
    for source in evaluation["decisions"]:
        if source.get("f1_decision_outcome") != DecisionQualityEngine.APPROVED:
            continue
        record = {**source, "decision_id": source["f1_decision_id"], "candidate_id": F1.candidate_id,
                  "candidate_config_hash": F1.config_hash}
        plans[str(record["decision_at"])] = _plan(record, strategy_version=F1.config_hash)
    config = ReplayConfig(timeframe=str(evaluation["dataset"]["timeframe"]), costs=costs)
    return _replay_recorded_plans(
        available, plans, config=config,
        metadata={"data_provider": evaluation["dataset"]["provider"],
                  "decision_source": "MASTER_F1_CONFIRMATION_F1_COST_SCENARIO",
                  "decision_authority": DecisionQualityEngine.AUTHORITY,
                  "decision_version": DecisionQualityEngine.DECISION_VERSION,
                  "variants_evaluated": 1, "sample_role": "MASTER_F1_CONFIRMATION",
                  "split_id": "PRE_REGISTERED_CONFIRMATION",
                  "evaluation_window": f"{blocks[0].start.isoformat()}/{blocks[-1].end.isoformat()}"},
    )
