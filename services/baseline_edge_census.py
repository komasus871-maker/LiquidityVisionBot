"""Side-effect-free Phase 3A frozen-decision census.

This module is deliberately offline from recorder, PAPER, LIVE, Telegram, and
private exchange paths.  It materializes public candles first, then replays the
current Analyzer/TradePlan/DecisionQuality semantics over closed snapshots.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import pandas as pd

from services.analyzer import Analyzer
from services.data_integrity import DataIntegrityEngine
from services.decision_quality import DecisionQualityEngine
from services.market_context import MarketContextEngine
from services.research_replay import (
    EntryPolicy, HistoricalReplayEngine, PerformanceAttribution, ReplayConfig,
    ReplayCostModel, ReplayOutcome, _as_utc,
)
from services.trade_plan_integrity import TradePlanIntegrity
from utils.timeframe import timeframe_seconds


PHASE3_SCHEMA_VERSION = "phase3-baseline-v1"
MIN_COMPARABLE_SAMPLE = 30
DEFAULT_SYMBOLS = ("BTC", "ETH", "SOL")  # First three configured WATCHLIST symbols, fixed before outcomes.
DEFAULT_TIMEFRAME = "1h"


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()[:16]


def _setup_key(analysis: Mapping[str, Any]) -> str:
    # Mirrors SignalRecorder's pure key logic without constructing its DB-backed service.
    import re
    parts = (analysis.get("structure"), analysis.get("choch"), analysis.get("sweep"),
             (analysis.get("order_block") or ""), (analysis.get("premium") or {}).get("zone"))
    return " | ".join(
        re.sub(r"\s+", " ", re.sub(r"[-+]?\d+(?:\.\d)?", "", re.sub(r"\([^)]*\)", "", str(x).replace("✅", "")))).strip(" -|")
        for x in parts if x
    )


@dataclass(frozen=True)
class FrozenBaselineConfig:
    payload: dict[str, Any]
    config_hash: str

    @classmethod
    def capture(cls, *, symbols: Iterable[str] = DEFAULT_SYMBOLS, timeframe: str = DEFAULT_TIMEFRAME,
                replay_config: ReplayConfig | None = None, repository: str = ".") -> "FrozenBaselineConfig":
        replay_config = replay_config or ReplayConfig(timeframe=timeframe)
        sources = {
            name: _hash(inspect.getsource(target)) for name, target in {
                "Analyzer": Analyzer, "TradePlanIntegrity": TradePlanIntegrity,
                "DecisionQualityEngine": DecisionQualityEngine,
            }.items()
        }
        try:
            commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip()
            dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=repository, text=True).splitlines()
        except (OSError, subprocess.CalledProcessError):
            commit, dirty = "UNAVAILABLE", []
        payload = {
            "schema": PHASE3_SCHEMA_VERSION, "git_commit": commit,
            "working_tree_identity": _hash(dirty), "source_identities": sources,
            "decision_authority": DecisionQualityEngine.AUTHORITY,
            "decision_version": DecisionQualityEngine.DECISION_VERSION,
            "analyzer_constants": {"min_rr": Analyzer.MIN_RR, "edge_neutral": Analyzer.EDGE_NEUTRAL},
            "trade_plan": {"targets": "1R/2R/3R", "entry": "runtime TradePlanIntegrity"},
            "symbols": [str(x).upper() for x in symbols], "timeframes": [timeframe],
            "strategy_modules": ["Analyzer", "TradePlanIntegrity", "DecisionQualityEngine"],
            "replay_engine": "historical-replay-v1", "replay_config": asdict(replay_config),
        }
        return cls(payload=payload, config_hash=_hash(payload))


@dataclass(frozen=True)
class DatasetManifest:
    dataset_id: str
    provider: str
    instrument: str
    timeframe: str
    start: str
    end: str
    candle_count: int
    retrieved_at: str
    integrity_status: str
    content_hash: str
    schema_version: str = PHASE3_SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class DatasetStore:
    def __init__(self, root: str | Path = "research_artifacts/phase3"):
        self.root = Path(root)
        self.integrity = DataIntegrityEngine()

    @staticmethod
    def content_hash(frame: pd.DataFrame) -> str:
        canonical = frame.loc[:, ["time", "open", "high", "low", "close", "volume"]]
        return hashlib.sha256(canonical.to_csv(index=False, date_format="%Y-%m-%dT%H:%M:%S.%f%z").encode()).hexdigest()

    def materialize(self, frame: pd.DataFrame, *, provider: str, instrument: str, timeframe: str,
                    retrieved_at: datetime | None = None) -> DatasetManifest:
        seconds = timeframe_seconds(timeframe)
        if not seconds or "time" not in frame:
            raise ValueError("dataset requires canonical timeframe and UTC candle timestamps")
        timestamps = pd.to_datetime(frame["time"], utc=True, errors="coerce")
        reference = timestamps.max() + pd.Timedelta(seconds=seconds)
        result = self.integrity.prepare_market_frame(
            frame, timeframe=timeframe, minimum_history=221, reference_time=reference,
            require_freshness=False, require_time_integrity=True,
        )
        if not result.valid:
            raise ValueError(f"dataset rejected: {result.status}/{result.code}")
        canonical = result.frame.reset_index(drop=True)
        digest = self.content_hash(canonical)
        dataset_id = digest[:16]
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{instrument.upper()}_{timeframe}_{dataset_id}.csv"
        canonical.to_csv(path, index=False, date_format="%Y-%m-%dT%H:%M:%S.%f%z")
        manifest = DatasetManifest(dataset_id, provider, instrument.upper(), timeframe,
                                   _as_utc(canonical.iloc[0]["time"]).isoformat(),
                                   _as_utc(canonical.iloc[-1]["time"]).isoformat(), len(canonical),
                                   (retrieved_at or datetime.now(timezone.utc)).isoformat(),
                                   result.status, digest)
        (self.root / f"{dataset_id}.manifest.json").write_text(json.dumps(manifest.as_dict(), indent=2, sort_keys=True), encoding="utf-8")
        return manifest

    async def fetch_okx_recent(self, *, instrument: str, timeframe: str, limit: int = 1000) -> DatasetManifest:
        """Fetch public OKX candles once; subsequent experiments read the artifact."""
        # Keep the offline research/replay path independent of optional network
        # transports.  The provider is required only when materializing a new
        # public dataset, never when loading or replaying an immutable one.
        from services.providers.okx import OKXProvider
        frame = await OKXProvider().get_klines(instrument, interval=timeframe, limit=limit)
        return self.materialize(frame, provider="OKX_PUBLIC_SWAP", instrument=instrument, timeframe=timeframe)

    def load(self, manifest: DatasetManifest) -> pd.DataFrame:
        path = self.root / f"{manifest.instrument}_{manifest.timeframe}_{manifest.dataset_id}.csv"
        frame = pd.read_csv(path)
        frame["time"] = pd.to_datetime(frame["time"], utc=True)
        if self.content_hash(frame) != manifest.content_hash:
            raise ValueError("dataset content hash mismatch")
        return frame


class _ResearchMemory:
    """Drop-in DecisionQuality memory that intentionally does not read or write runtime JSONL."""
    def remember(self, symbol: str, timeframe: str, analysis: Mapping[str, Any]) -> dict[str, Any]:
        return {"samples": 0, "state": "RESEARCH_ISOLATED", "summary": "Runtime market memory excluded from replay."}


class BaselineCensusRunner:
    """Runs the frozen decision stack and Phase 2B execution against one materialized dataset."""
    def __init__(self, frozen: FrozenBaselineConfig, *, replay_config: ReplayConfig | None = None,
                 analyzer: Any | None = None, decision_quality: DecisionQualityEngine | None = None):
        self.frozen = frozen
        timeframe = frozen.payload["timeframes"][0]
        self.replay = HistoricalReplayEngine(replay_config or ReplayConfig(timeframe=timeframe))
        self.analyzer = analyzer or Analyzer()
        self.decision_quality = decision_quality or DecisionQualityEngine()
        self.decision_quality.memory = _ResearchMemory()
        self.market_context = MarketContextEngine()

    def run(self, frame: pd.DataFrame, manifest: DatasetManifest, *, anchor_frame: pd.DataFrame | None = None) -> dict[str, Any]:
        prepared = self.replay.prepare(frame)
        anchor = self.replay.prepare(anchor_frame) if anchor_frame is not None else None
        decisions: list[dict[str, Any]] = []
        duration = timeframe_seconds(manifest.timeframe) or 0

        def factory(snapshot: pd.DataFrame) -> Mapping[str, Any] | None:
            timestamp = _as_utc(snapshot.iloc[-1]["time"] + pd.Timedelta(seconds=duration))
            analysis = self.analyzer.analyze(snapshot, symbol=manifest.instrument, timeframe=manifest.timeframe,
                                             source="PHASE3_BASELINE", use_cache=False, research_envelope=False)
            # Manual runtime's BTC context is reproduced only when a synchronized
            # anchor dataset was supplied; no remote or future context is fetched.
            if anchor is not None and manifest.instrument != "BTC":
                anchor_snapshot = anchor.loc[anchor["time"] <= snapshot.iloc[-1]["time"]].copy()
                if len(anchor_snapshot) >= 220:
                    btc = self.analyzer.analyze(anchor_snapshot, symbol="BTC", timeframe=manifest.timeframe,
                                                source="PHASE3_BTC_CONTEXT", use_cache=False, research_envelope=False)
                    analysis = self.market_context.enrich(analysis, symbol=manifest.instrument, btc=btc)
            # Existing DB-backed probability history is deliberately neutralized:
            # Phase 2B proved it is not a replay-certified historical dataset.
            analysis.update({"historical_probability": {"samples": 0, "sufficient": False},
                             "similar_cases": [], "similar_stats": {"samples": 0},
                             "weighted_similarity": {"samples": 0, "estimated": False},
                             "historical_intelligence": {"samples": 0, "estimated": False,
                                                         "reliability": "Unavailable"},
                             "timestamp": timestamp.isoformat(), "symbol": manifest.instrument,
                             "timeframe": manifest.timeframe})
            analysis = self.decision_quality.enrich(analysis, source="PHASE3_BASELINE")
            decision_id = f"{manifest.instrument}:{manifest.timeframe}:{timestamp.isoformat()}"
            record = _decision_record(analysis, decision_id, timestamp, manifest, self.frozen.config_hash)
            decisions.append(record)
            if record["decision_outcome"] != DecisionQualityEngine.APPROVED:
                return None
            return {"signal_id": decision_id, "strategy_version": self.frozen.config_hash,
                    "symbol": manifest.instrument, "direction": record["direction"],
                    "entry": record["entry"], "stop": record["stop"], "tp1": record["tp1"],
                    "entry_policy": EntryPolicy.MARKET_NEXT_OPEN.value if record["entry_type"] == "MARKET_READY"
                    else EntryPolicy.LIMIT_AFTER_DECISION.value, "metadata": record}

        outcomes = self.replay.run(prepared, factory, metadata={
            "data_provider": manifest.provider, "decision_source": "PHASE3_BASELINE",
            "decision_authority": DecisionQualityEngine.AUTHORITY,
            "decision_version": DecisionQualityEngine.DECISION_VERSION,
            "variants_evaluated": 1, "sample_role": "FROZEN_BASELINE",
            "split_id": "PREDECLARED_60_20_20", "evaluation_window": f"{manifest.start}/{manifest.end}",
        })
        outcome_map = {item.signal_id: item for item in outcomes}
        for decision in decisions:
            outcome = outcome_map.get(decision["decision_id"])
            decision["outcome"] = outcome.as_dict() if outcome else None
            decision["sample_role"] = _split_role(decision["decision_at"], prepared, manifest.timeframe)
        return build_census(self.frozen, manifest, decisions, outcomes, prepared)


def _decision_record(analysis: Mapping[str, Any], decision_id: str, timestamp: datetime,
                     manifest: DatasetManifest, config_hash: str) -> dict[str, Any]:
    regime = analysis.get("market_regime") or {}
    return {"decision_id": decision_id, "symbol": manifest.instrument, "timeframe": manifest.timeframe,
            "decision_at": timestamp.isoformat(), "strategy_version": config_hash,
            "strategy_family": "UNIFIED_ANALYZER_MIXED", "direction": str(analysis.get("direction") or "UNKNOWN"),
            "decision_outcome": str(analysis.get("decision_outcome") or "NO_TRADE"),
            "confidence": analysis.get("confidence"), "probability": analysis.get("probability"),
            "quality_score": analysis.get("setup_score"), "veto_reasons": list(analysis.get("decision_veto_reasons") or []),
            "market_regime": str(regime.get("code") or "UNKNOWN") if isinstance(regime, Mapping) else str(regime),
            "data_quality": (analysis.get("decision_data_quality") or "UNKNOWN"),
            "entry": analysis.get("entry"), "stop": analysis.get("stop"), "tp1": analysis.get("tp1"),
            "rr": analysis.get("rr"), "entry_type": analysis.get("entry_type"),
            "decision_source": analysis.get("decision_source"), "decision_authority": analysis.get("decision_authority"),
            "decision_version": analysis.get("decision_version"), "dataset_id": manifest.dataset_id}


def _split_role(decision_at: str, frame: pd.DataFrame, timeframe: str) -> str:
    value = pd.Timestamp(decision_at)
    start, end = frame["time"].iloc[0], frame["time"].iloc[-1] + pd.Timedelta(seconds=timeframe_seconds(timeframe) or 0)
    fraction = (value - start) / (end - start) if end > start else 0
    return "DEVELOPMENT" if fraction < .60 else "VALIDATION" if fraction < .80 else "FINAL_TEST"


def _group(outcomes: Iterable[ReplayOutcome], dimension: str) -> dict[str, dict[str, Any]]:
    groups = PerformanceAttribution.group(outcomes, dimension)
    for value in groups.values():
        value["low_sample"] = value["trade_count"] < MIN_COMPARABLE_SAMPLE
    return groups


def confidence_calibration(decisions: Iterable[Mapping[str, Any]], outcomes: Iterable[ReplayOutcome]) -> dict[str, dict[str, Any]]:
    outcome_map = {item.signal_id: item for item in outcomes if item.simulated_fill is not None}
    buckets: dict[str, list[ReplayOutcome]] = {}
    for decision in decisions:
        outcome = outcome_map.get(str(decision.get("decision_id")))
        if not outcome:
            continue
        try:
            floor = int(float(decision.get("confidence")) // 10 * 10)
        except (TypeError, ValueError):
            floor = -1
        label = "UNKNOWN" if floor < 0 else ("80+" if floor >= 80 else f"{floor:02d}-{floor + 9:02d}")
        buckets.setdefault(label, []).append(outcome)
    return {label: {**PerformanceAttribution.metrics(items), "low_sample": len(items) < MIN_COMPARABLE_SAMPLE}
            for label, items in sorted(buckets.items())}


def forensic_label(outcome: ReplayOutcome, frame: pd.DataFrame, *, post_exit_bars: int = 24) -> dict[str, Any]:
    """Research-only loss/win context; post-exit movement never changes outcome fields."""
    if outcome.simulated_fill is None:
        return {"classification": "UNFILLED", "post_exit_recovery": False}
    flags = set(outcome.ambiguity_flags)
    material_ambiguity = {"STOP_TARGET_SAME_CANDLE_CONSERVATIVE_STOP_FIRST", "LIMIT_ENTRY_BAR_STOP_CONSERVATIVE"}
    if flags & material_ambiguity:
        label = "GAP_OR_AMBIGUITY_AFFECTED"
    elif outcome.net_pnl < 0 and (outcome.mfe_r or 0) <= .25 and (outcome.mae_r or 0) >= .5:
        label = "IMMEDIATE_ADVERSE"
    elif outcome.net_pnl < 0 and (outcome.mfe_r or 0) >= .75:
        label = "PRIOR_MFE_LOSS"
    elif outcome.net_pnl < 0 and (outcome.mfe_r or 0) <= .25 and (outcome.mae_r or 0) <= .25:
        label = "LOW_MOVEMENT_CHOP"
    elif outcome.net_pnl > 0:
        label = "WINNER"
    else:
        label = "OTHER"
    recovery = False
    if outcome.net_pnl < 0 and outcome.exit_at:
        after = frame.loc[pd.to_datetime(frame["time"], utc=True) >= pd.Timestamp(outcome.exit_at)]
        after = after.iloc[:post_exit_bars]
        target = outcome.targets[0]
        recovery = bool((after["high"] >= target).any()) if outcome.direction == "LONG" else bool((after["low"] <= target).any())
    return {"classification": label, "post_exit_recovery": recovery,
            "mfe_captured_pct": round(min(100.0, max(0.0, (outcome.mfe_r or 0) * 100)), 6) if outcome.net_pnl > 0 else None,
            "time_to_exit_bars": (outcome.provenance or {}).get("bars_held")}


def build_census(frozen: FrozenBaselineConfig, manifest: DatasetManifest, decisions: list[dict[str, Any]],
                 outcomes: list[ReplayOutcome], frame: pd.DataFrame) -> dict[str, Any]:
    approved = [item for item in decisions if item["decision_outcome"] == DecisionQualityEngine.APPROVED]
    metrics = PerformanceAttribution.metrics(outcomes)
    groups = {dimension: _group(outcomes, dimension) for dimension in (
        "strategy_version", "symbol", "timeframe", "direction", "market_regime", "confidence_bucket",
        "probability_bucket", "rr_bucket", "hour", "session", "decision_source", "exit_reason")}
    return {"schema": PHASE3_SCHEMA_VERSION, "frozen_config": frozen.payload, "config_hash": frozen.config_hash,
            "dataset": manifest.as_dict(), "decision_count": len(decisions), "approved_count": len(approved),
            "no_trade_count": len(decisions) - len(approved),
            "trade_rate": round(len(approved) / len(decisions) * 100, 6) if decisions else 0.0,
            "metrics": metrics, "decisions": decisions, "outcomes": [item.as_dict() for item in outcomes],
            "attribution": groups, "confidence_calibration": confidence_calibration(decisions, outcomes),
            "forensics": {item.signal_id: forensic_label(item, frame) for item in outcomes},
            "limitations": ["ProbabilityEngine historical DB evidence excluded as unreplay-certified.",
                            "No private data, SignalRecorder, PAPER, LIVE, copy, or Telegram path was invoked.",
                            "OHLC replay uses Phase 2B modeled costs and conservative intrabar assumptions."],
            "run_id": _hash({"config": frozen.config_hash, "dataset": manifest.dataset_id})}


def compare_baselines(baseline: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Fixed Phase 3B comparison guard; no ranking solely from WR/DD/tiny samples."""
    left, right = baseline.get("metrics") or {}, candidate.get("metrics") or {}
    reasons: list[str] = []
    if int(right.get("trade_count") or 0) < MIN_COMPARABLE_SAMPLE:
        reasons.append("CANDIDATE_INSUFFICIENT_SAMPLE")
    if float(right.get("expectancy") or 0) <= float(left.get("expectancy") or 0):
        reasons.append("NET_EXPECTANCY_NOT_IMPROVED")
    if float(right.get("expectancy_r") or 0) <= float(left.get("expectancy_r") or 0):
        reasons.append("EXPECTANCY_R_NOT_IMPROVED")
    if (right.get("profit_factor") is None or left.get("profit_factor") is not None
            and float(right["profit_factor"]) < float(left["profit_factor"])):
        reasons.append("PROFIT_FACTOR_NOT_IMPROVED")
    if not candidate.get("final_test_metrics") or not candidate.get("stability"):
        reasons.append("UNSEEN_STABILITY_EVIDENCE_REQUIRED")
    return {"verdict": "NOT_UNAMBIGUOUSLY_IMPROVED" if reasons else "CANDIDATE_IMPROVED_PENDING_REVIEW",
            "reasons": reasons, "baseline_trade_count": left.get("trade_count"),
            "candidate_trade_count": right.get("trade_count")}


def write_census_artifact(census: Mapping[str, Any], root: str | Path = "research_artifacts/phase3") -> Path:
    path = Path(root)
    path.mkdir(parents=True, exist_ok=True)
    output = path / f"baseline-census-{census['run_id']}.json"
    output.write_text(json.dumps(census, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return output
