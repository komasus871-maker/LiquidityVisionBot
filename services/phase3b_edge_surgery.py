"""Research-only Phase 3B candidate generation over immutable candle artifacts.

No production analysis, recording, PAPER, LIVE, copy, or messaging path imports
this module. Candidates are explicit overlays around the frozen decision stack.
"""
from __future__ import annotations

import hashlib
import json
import random
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd

from services.analyzer import Analyzer
from services.baseline_edge_census import DatasetManifest, _ResearchMemory
from services.data_integrity import DataIntegrityEngine
from services.decision_quality import DecisionQualityEngine
from services.market_context import MarketContextEngine
from services.research_replay import (
    ExitProtectionConfig, HistoricalReplayEngine, PerformanceAttribution,
    ReplayConfig, ReplayCostModel, ReplayOutcome, _as_utc,
)
from utils.timeframe import timeframe_seconds


PHASE3B_SCHEMA_VERSION = "phase3b-edge-surgery-v1"
PARENT_BASELINE_CONFIG = "84533946dfbbbf65"
LEGACY_SEEN_START = datetime(2026, 8, 3, 1, tzinfo=timezone.utc)
SETTLEMENT_BARS = 144  # Maximum 24-bar entry expiry + 120-bar holding period.
MIN_COMPARABLE_SAMPLE = 30


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()[:16]


class SplitRole(StrEnum):
    DEVELOPMENT = "DEVELOPMENT"
    VALIDATION = "VALIDATION"
    BLIND_HOLDOUT = "BLIND_HOLDOUT"
    LEGACY_SEEN_TEST = "LEGACY_SEEN_TEST"


@dataclass(frozen=True)
class DecisionInterval:
    role: SplitRole
    start: datetime
    end: datetime
    access_end: datetime

    def includes(self, value: datetime) -> bool:
        value = _as_utc(value)
        return self.start <= value <= self.end


@dataclass(frozen=True)
class TemporalExperimentDesign:
    timeframe: str
    legacy_seen_start: datetime
    settlement_bars: int
    intervals: tuple[DecisionInterval, ...]

    @classmethod
    def from_frame(
        cls, frame: pd.DataFrame, *, timeframe: str = "1h", legacy_seen_start: datetime = LEGACY_SEEN_START,
        warmup_bars: int = 220, settlement_bars: int = SETTLEMENT_BARS,
    ) -> "TemporalExperimentDesign":
        seconds = timeframe_seconds(timeframe)
        if not seconds:
            raise ValueError("Phase 3B requires a canonical timeframe")
        times = pd.to_datetime(frame["time"], utc=True).sort_values().drop_duplicates().reset_index(drop=True)
        unseen = times[times < pd.Timestamp(legacy_seen_start)]
        available = len(unseen) - warmup_bars - settlement_bars * 3
        if available < 1000:
            raise ValueError("expanded unseen history is insufficient for Phase 3B temporal design")
        development_count = int(available * .55)
        validation_count = int(available * .20)
        blind_count = available - development_count - validation_count
        first = warmup_bars
        step = timedelta(seconds=seconds)

        def interval(role: SplitRole, start_index: int, count: int) -> DecisionInterval:
            end_index = start_index + count - 1
            start = _as_utc(times.iloc[start_index] + pd.Timedelta(seconds=seconds))
            end = _as_utc(times.iloc[end_index] + pd.Timedelta(seconds=seconds))
            access_end = _as_utc(times.iloc[end_index + settlement_bars])
            return DecisionInterval(role, start, end, access_end)

        development = interval(SplitRole.DEVELOPMENT, first, development_count)
        validation_start = first + development_count + settlement_bars
        validation = interval(SplitRole.VALIDATION, validation_start, validation_count)
        blind_start = validation_start + validation_count + settlement_bars
        blind = interval(SplitRole.BLIND_HOLDOUT, blind_start, blind_count)
        legacy_end = _as_utc(times.iloc[-1] + pd.Timedelta(seconds=seconds))
        legacy = DecisionInterval(SplitRole.LEGACY_SEEN_TEST, _as_utc(legacy_seen_start), legacy_end, legacy_end)
        return cls(timeframe, _as_utc(legacy_seen_start), settlement_bars, (development, validation, blind, legacy))

    def interval(self, role: SplitRole) -> DecisionInterval:
        for item in self.intervals:
            if item.role == role:
                return item
        raise ValueError(f"unknown Phase 3B role: {role}")

    def as_dict(self) -> dict[str, Any]:
        return {
            "timeframe": self.timeframe, "legacy_seen_start": self.legacy_seen_start.isoformat(),
            "settlement_bars": self.settlement_bars,
            "intervals": [{"role": item.role, "start": item.start.isoformat(), "end": item.end.isoformat(),
                           "access_end": item.access_end.isoformat()} for item in self.intervals],
        }


@dataclass
class ExperimentAccess:
    """Prevents blind/legacy metrics until a validation-selected candidate freezes."""
    finalist_id: str | None = None
    blind_evaluated: bool = False

    def freeze_finalist(self, candidate_id: str) -> None:
        if self.finalist_id is not None and self.finalist_id != candidate_id:
            raise ValueError("a different Phase 3B finalist is already frozen")
        self.finalist_id = candidate_id

    def permit(self, role: SplitRole, candidate_id: str) -> None:
        if role == SplitRole.BLIND_HOLDOUT and candidate_id != self.finalist_id:
            raise PermissionError("BLIND_HOLDOUT is unavailable until this candidate is frozen")
        if role == SplitRole.LEGACY_SEEN_TEST and not self.blind_evaluated:
            raise PermissionError("LEGACY_SEEN_TEST is unavailable before blind evaluation")
        if role == SplitRole.BLIND_HOLDOUT:
            self.blind_evaluated = True


@dataclass(frozen=True)
class CandidateSpec:
    candidate_id: str
    hypothesis_id: str
    parameters: Mapping[str, Any]
    parent_config_hash: str = PARENT_BASELINE_CONFIG

    @property
    def config_hash(self) -> str:
        return _stable_hash({"schema": PHASE3B_SCHEMA_VERSION, "candidate": self.candidate_id,
                             "hypothesis": self.hypothesis_id, "parameters": dict(self.parameters),
                             "parent": self.parent_config_hash})

    @classmethod
    def baseline(cls) -> "CandidateSpec":
        return cls("BASELINE", "BASELINE", {})


def analyzer_attribution(analysis: Mapping[str, Any]) -> dict[str, Any]:
    """Expose Analyzer evidence already used; never synthesize a strategy label."""
    return {
        "strategy_family": "UNIFIED_ANALYZER_MIXED",
        "direction": analysis.get("direction"),
        "directional_edge": analysis.get("directional_edge"),
        "direction_score": analysis.get("direction_score"),
        "execution_readiness": analysis.get("execution_readiness"),
        "direction_breakdown": list(analysis.get("direction_breakdown") or []),
        "score_components": list(analysis.get("score_components") or []),
        "strongest_drivers": list(analysis.get("strongest_drivers") or []),
        "biggest_blockers": list(analysis.get("biggest_blockers") or []),
        "market_regime": dict(analysis.get("market_regime") or {}),
        "structure": analysis.get("structure"), "choch": analysis.get("choch"),
        "displacement": analysis.get("displacement"), "volume_ratio": analysis.get("volume_ratio"),
    }


def _candidate_accepts(candidate: CandidateSpec, analysis: Mapping[str, Any]) -> tuple[bool, str | None]:
    params = dict(candidate.parameters)
    direction = str(analysis.get("direction") or "")
    edge = abs(float(analysis.get("directional_edge") or 0.0))
    readiness = float(analysis.get("execution_readiness") or 0.0)
    confidence = float(analysis.get("confidence") or 0.0)
    if direction == "SHORT" and params.get("short_min_directional_edge") is not None:
        if edge < float(params["short_min_directional_edge"]):
            return False, "CANDIDATE_SHORT_DIRECTIONAL_EDGE"
    if params.get("min_directional_edge") is not None and edge < float(params["min_directional_edge"]):
        return False, "CANDIDATE_DIRECTIONAL_EDGE"
    if params.get("min_execution_readiness") is not None and readiness < float(params["min_execution_readiness"]):
        return False, "CANDIDATE_EXECUTION_READINESS"
    if params.get("max_confidence") is not None and confidence > float(params["max_confidence"]):
        return False, "CANDIDATE_CONFIDENCE_CEILING"
    return True, None


def _outcome_from_value(value: ReplayOutcome | Mapping[str, Any]) -> ReplayOutcome:
    """Deserialize an artifact outcome without changing its evidence fields."""
    if isinstance(value, ReplayOutcome):
        return value
    payload = dict(value)
    payload["targets"] = tuple(payload.get("targets") or ())
    payload["ambiguity_flags"] = tuple(payload.get("ambiguity_flags") or ())
    payload["provenance"] = dict(payload.get("provenance") or {})
    return ReplayOutcome(**payload)


def derive_admission_overlay(
    baseline_evaluation: Mapping[str, Any], candidate: CandidateSpec,
) -> dict[str, Any]:
    """Apply an admission-only candidate to an already evaluated role.

    This deliberately reuses only the baseline's decision-time attribution and
    historical outcomes.  It is valid for admission overlays because accepted
    signals retain their original entry, exits, and cost model; exit-changing
    hypotheses must use a fresh replay instead.
    """
    if any(key in candidate.parameters for key in ("exit_protection",)):
        raise ValueError("exit-changing candidates require a chronological replay")
    baseline_outcomes = {
        item.signal_id: item for item in
        (_outcome_from_value(value) for value in baseline_evaluation.get("outcomes", []))
    }
    decisions: list[dict[str, Any]] = []
    outcomes: list[ReplayOutcome] = []
    for source in baseline_evaluation.get("decisions", []):
        record = dict(source)
        analysis = {
            "direction": record.get("direction"), "confidence": record.get("confidence"),
            **dict(record.get("attribution") or {}),
        }
        baseline_approved = record.get("baseline_decision_outcome") == DecisionQualityEngine.APPROVED
        accepted, rejection = _candidate_accepts(candidate, analysis) if baseline_approved else (False, None)
        old_id = str(record["decision_id"])
        new_id = old_id.replace("BASELINE:", f"{candidate.candidate_id}:", 1)
        record.update({
            "decision_id": new_id, "candidate_id": candidate.candidate_id,
            "candidate_config_hash": candidate.config_hash,
            "parent_config_hash": candidate.parent_config_hash,
            "candidate_decision_outcome": DecisionQualityEngine.APPROVED if accepted else "NO_TRADE",
            "candidate_rejection": rejection,
        })
        old_outcome = baseline_outcomes.get(old_id)
        if accepted and old_outcome is not None:
            provenance = {**old_outcome.provenance, "candidate_id": candidate.candidate_id,
                          "candidate_config_hash": candidate.config_hash,
                          "decision_source": f"PHASE3B_{candidate.candidate_id}",
                          "derived_admission_overlay": True}
            outcome = replace(old_outcome, signal_id=new_id, strategy_version=candidate.config_hash,
                              provenance=provenance)
            outcomes.append(outcome)
            record["outcome"] = outcome.as_dict()
        else:
            record["outcome"] = None
        decisions.append(record)
    return {
        "schema": PHASE3B_SCHEMA_VERSION,
        "candidate": {"id": candidate.candidate_id, "hypothesis": candidate.hypothesis_id,
                      "parameters": dict(candidate.parameters), "config_hash": candidate.config_hash,
                      "parent_config_hash": candidate.parent_config_hash},
        "role": baseline_evaluation["role"], "interval": dict(baseline_evaluation["interval"]),
        "dataset": dict(baseline_evaluation["dataset"]), "decisions": decisions, "outcomes": outcomes,
        "metrics": PerformanceAttribution.metrics(outcomes), "regime_distribution": regime_distribution(decisions),
        "derivation": "ADMISSION_ONLY_FROM_BASELINE_DECISION_TIME_ATTRIBUTION",
    }


def fit_development_confidence_calibration(
    decisions: Iterable[Mapping[str, Any]], *, version: str = "pav-v1",
) -> dict[str, Any]:
    """Fit a deterministic monotone win-probability map from DEVELOPMENT only.

    Pool-adjacent-violators is intentionally small and parameter-free.  The
    returned artifact is diagnostic; it does not mutate Analyzer confidence or
    authorize an admission change.
    """
    buckets: dict[int, list[float]] = {}
    for record in decisions:
        outcome = record.get("outcome") or {}
        if outcome.get("simulated_fill") is None:
            continue
        bucket = int(float(record.get("confidence") or 0) // 5 * 5)
        buckets.setdefault(bucket, []).append(float(outcome.get("net_r") or 0) > 0)
    blocks: list[dict[str, Any]] = []
    for bucket in sorted(buckets):
        values = buckets[bucket]
        blocks.append({"low": bucket, "high": bucket + 4, "wins": sum(values), "count": len(values)})
        while len(blocks) > 1 and blocks[-2]["wins"] / blocks[-2]["count"] > blocks[-1]["wins"] / blocks[-1]["count"]:
            right, left = blocks.pop(), blocks.pop()
            blocks.append({"low": left["low"], "high": right["high"],
                           "wins": left["wins"] + right["wins"], "count": left["count"] + right["count"]})
    return {"version": version, "fit_role": SplitRole.DEVELOPMENT,
            "buckets": [{"confidence_range": f"{item['low']}-{item['high']}", "samples": item["count"],
                         "win_probability": round(item["wins"] / item["count"], 12)} for item in blocks]}


def _candidate_replay_config(base: ReplayConfig, candidate: CandidateSpec, costs: ReplayCostModel | None) -> ReplayConfig:
    params = dict(candidate.parameters)
    protection = params.get("exit_protection")
    if protection is not None and not isinstance(protection, ExitProtectionConfig):
        protection = ExitProtectionConfig(**dict(protection))
    return replace(base, costs=costs or base.costs, exit_protection=protection)


class Phase3BRunner:
    """Side-effect-free candidate replay with role-gated temporal access."""
    def __init__(self, design: TemporalExperimentDesign, *, replay_config: ReplayConfig | None = None):
        self.design = design
        self.replay_config = replay_config or ReplayConfig(timeframe=design.timeframe)
        self.integrity = DataIntegrityEngine()

    def evaluate(
        self, frame: pd.DataFrame, manifest: DatasetManifest, *, role: SplitRole, candidate: CandidateSpec,
        access: ExperimentAccess, anchor_frame: pd.DataFrame | None = None, costs: ReplayCostModel | None = None,
    ) -> dict[str, Any]:
        access.permit(role, candidate.candidate_id)
        interval = self.design.interval(role)
        available = frame.loc[pd.to_datetime(frame["time"], utc=True) <= pd.Timestamp(interval.access_end)].copy()
        if available.empty:
            raise ValueError("Phase 3B interval has no visible candles")
        config = _candidate_replay_config(self.replay_config, candidate, costs)
        replay = HistoricalReplayEngine(config)
        prepared = replay.prepare(available)
        anchor = replay.prepare(anchor_frame.loc[
            pd.to_datetime(anchor_frame["time"], utc=True) <= pd.Timestamp(interval.access_end)
        ].copy()) if anchor_frame is not None else None
        analyzer, quality, context = Analyzer(), DecisionQualityEngine(), MarketContextEngine()
        quality.memory = _ResearchMemory()
        decisions: list[dict[str, Any]] = []
        seconds = timeframe_seconds(manifest.timeframe)
        if not seconds:
            raise ValueError("invalid Phase 3B timeframe")

        def factory(snapshot: pd.DataFrame) -> Mapping[str, Any] | None:
            decision_at = _as_utc(snapshot.iloc[-1]["time"] + pd.Timedelta(seconds=seconds))
            if not interval.includes(decision_at):
                return None
            analysis = analyzer.analyze(snapshot, symbol=manifest.instrument, timeframe=manifest.timeframe,
                                        source=f"PHASE3B_{candidate.candidate_id}", use_cache=False,
                                        research_envelope=False)
            if anchor is not None and manifest.instrument != "BTC":
                anchor_snapshot = anchor.loc[anchor["time"] <= snapshot.iloc[-1]["time"]].copy()
                if len(anchor_snapshot) >= config.warmup_bars:
                    btc = analyzer.analyze(anchor_snapshot, symbol="BTC", timeframe=manifest.timeframe,
                                           source="PHASE3B_BTC_CONTEXT", use_cache=False, research_envelope=False)
                    analysis = context.enrich(analysis, symbol=manifest.instrument, btc=btc)
            analysis.update({"historical_probability": {"samples": 0, "sufficient": False},
                             "similar_cases": [], "similar_stats": {"samples": 0},
                             "weighted_similarity": {"samples": 0, "estimated": False},
                             "historical_intelligence": {"samples": 0, "estimated": False,
                                                         "reliability": "Unavailable"},
                             "timestamp": decision_at.isoformat(), "symbol": manifest.instrument,
                             "timeframe": manifest.timeframe})
            analysis = quality.enrich(analysis, source=f"PHASE3B_{candidate.candidate_id}")
            decision_id = f"{candidate.candidate_id}:{manifest.instrument}:{manifest.timeframe}:{decision_at.isoformat()}"
            approved = str(analysis.get("decision_outcome")) == DecisionQualityEngine.APPROVED
            accepted, rejection = _candidate_accepts(candidate, analysis) if approved else (False, None)
            record = {
                "decision_id": decision_id, "symbol": manifest.instrument, "timeframe": manifest.timeframe,
                "decision_at": decision_at.isoformat(), "candidate_id": candidate.candidate_id,
                "candidate_config_hash": candidate.config_hash, "parent_config_hash": candidate.parent_config_hash,
                "baseline_decision_outcome": analysis.get("decision_outcome"),
                "candidate_decision_outcome": DecisionQualityEngine.APPROVED if approved and accepted else "NO_TRADE",
                "candidate_rejection": rejection, "direction": analysis.get("direction"),
                "confidence": analysis.get("confidence"), "probability": analysis.get("probability"),
                "quality_score": analysis.get("setup_score"), "entry": analysis.get("entry"),
                "stop": analysis.get("stop"), "tp1": analysis.get("tp1"), "rr": analysis.get("rr"),
                "entry_type": analysis.get("entry_type"), "decision_veto_reasons": analysis.get("decision_veto_reasons"),
                "attribution": analyzer_attribution(analysis), "dataset_id": manifest.dataset_id, "role": role,
            }
            decisions.append(record)
            if not approved or not accepted:
                return None
            return {"signal_id": decision_id, "strategy_version": candidate.config_hash,
                    "symbol": manifest.instrument, "direction": record["direction"], "entry": record["entry"],
                    "stop": record["stop"], "tp1": record["tp1"],
                    "entry_policy": "MARKET_NEXT_OPEN" if record["entry_type"] == "MARKET_READY"
                    else "LIMIT_AFTER_DECISION", "metadata": {**record, "market_regime":
                    (record["attribution"].get("market_regime") or {}).get("code"),
                    "decision_authority": DecisionQualityEngine.AUTHORITY,
                    "decision_version": DecisionQualityEngine.DECISION_VERSION,
                    "decision_source": f"PHASE3B_{candidate.candidate_id}"}}

        outcomes = replay.run(prepared, factory, metadata={
            "data_provider": manifest.provider, "decision_source": f"PHASE3B_{candidate.candidate_id}",
            "decision_authority": DecisionQualityEngine.AUTHORITY,
            "decision_version": DecisionQualityEngine.DECISION_VERSION, "variants_evaluated": 1,
            "sample_role": role, "split_id": role, "evaluation_window": f"{interval.start.isoformat()}/{interval.end.isoformat()}",
        })
        outcome_map = {item.signal_id: item for item in outcomes}
        for record in decisions:
            item = outcome_map.get(record["decision_id"])
            record["outcome"] = item.as_dict() if item else None
        return {"schema": PHASE3B_SCHEMA_VERSION, "candidate": {"id": candidate.candidate_id,
                "hypothesis": candidate.hypothesis_id, "parameters": dict(candidate.parameters),
                "config_hash": candidate.config_hash, "parent_config_hash": candidate.parent_config_hash},
                "role": role, "interval": {"start": interval.start.isoformat(), "end": interval.end.isoformat(),
                "access_end": interval.access_end.isoformat()}, "dataset": manifest.as_dict(), "decisions": decisions,
                "outcomes": outcomes, "metrics": PerformanceAttribution.metrics(outcomes),
                "regime_distribution": regime_distribution(decisions)}


def regime_distribution(decisions: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for decision in decisions:
        regime = str(((decision.get("attribution") or {}).get("market_regime") or {}).get("code") or "UNKNOWN")
        row = result.setdefault(regime, {"eligible": 0, "baseline_approved": 0, "candidate_approved": 0})
        row["eligible"] += 1
        row["baseline_approved"] += int(decision.get("baseline_decision_outcome") == DecisionQualityEngine.APPROVED)
        row["candidate_approved"] += int(decision.get("candidate_decision_outcome") == DecisionQualityEngine.APPROVED)
    return dict(sorted(result.items()))


def block_bootstrap_expectancy_r(
    outcomes: Iterable[ReplayOutcome], *, seed: int = 3103, block_size: int = 24, resamples: int = 500,
) -> dict[str, float | int | None]:
    """Deterministic temporal-block bootstrap for net-R expectancy uncertainty."""
    values = [float(item.net_r) for item in outcomes if item.simulated_fill is not None]
    if not values:
        return {"sample_size": 0, "estimate": None, "lower": None, "upper": None,
                "seed": seed, "block_size": block_size, "resamples": resamples}
    rng, draws, count = random.Random(seed), [], len(values)
    for _ in range(resamples):
        sample: list[float] = []
        while len(sample) < count:
            start = rng.randrange(count)
            sample.extend(values[(start + offset) % count] for offset in range(block_size))
        draws.append(sum(sample[:count]) / count)
    draws.sort()
    return {"sample_size": count, "estimate": round(sum(values) / count, 12),
            "lower": round(draws[int((resamples - 1) * .025)], 12),
            "upper": round(draws[int((resamples - 1) * .975)], 12), "seed": seed,
            "block_size": block_size, "resamples": resamples}


def write_experiment_register(records: Iterable[Mapping[str, Any]], root: str | Path = "research_artifacts/phase3b") -> Path:
    payload = {"schema": PHASE3B_SCHEMA_VERSION, "records": list(records)}
    payload["register_hash"] = _stable_hash(payload)
    output = Path(root) / f"experiment-register-{payload['register_hash']}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return output


def write_evaluation_artifact(
    evaluations: Iterable[Mapping[str, Any]], *, label: str, root: str | Path = "research_artifacts/phase3b",
) -> Path:
    """Persist complete research evidence without making it a Git artifact."""
    serialized: list[dict[str, Any]] = []
    for evaluation in evaluations:
        row = dict(evaluation)
        row["outcomes"] = [item.as_dict() if isinstance(item, ReplayOutcome) else item
                           for item in row.get("outcomes", [])]
        serialized.append(row)
    payload = {"schema": PHASE3B_SCHEMA_VERSION, "label": label, "evaluations": serialized}
    payload["artifact_hash"] = _stable_hash(payload)
    output = Path(root) / f"{label}-{payload['artifact_hash']}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return output
