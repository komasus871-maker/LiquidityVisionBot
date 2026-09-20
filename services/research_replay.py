"""Deterministic, side-effect-free historical replay truth primitives.

This module is intentionally separate from production signal creation and
execution.  It evaluates a supplied closed-candle dataset; it never queries an
exchange, writes a database row, changes a strategy, or grants trade authority.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Iterable, Mapping

import pandas as pd

from services.data_integrity import DataIntegrityEngine
from utils.timeframe import normalize_market_timeframe, timeframe_seconds


REPLAY_ENGINE_VERSION = "historical-replay-v1"


class EvaluationMode(str, Enum):
    CONCEPTUAL = "CONCEPTUAL"
    HISTORICAL_REPLAY = "HISTORICAL_REPLAY"
    PAPER = "PAPER"
    SHADOW = "SHADOW"
    LIVE = "LIVE"


class EntryPolicy(str, Enum):
    MARKET_NEXT_OPEN = "MARKET_NEXT_OPEN"
    LIMIT_AFTER_DECISION = "LIMIT_AFTER_DECISION"


class IntrabarPolicy(str, Enum):
    CONSERVATIVE_STOP_FIRST = "CONSERVATIVE_STOP_FIRST"


@dataclass(frozen=True)
class ReplayCostModel:
    fee_rate: float = 0.0005
    market_slippage_rate: float = 0.0003
    funding_rate_per_bar: float | None = None
    source: str = "MODELED_DEFAULT"

    def __post_init__(self) -> None:
        if self.fee_rate < 0 or self.market_slippage_rate < 0:
            raise ValueError("fee_rate and market_slippage_rate cannot be negative")

    @property
    def funding_status(self) -> str:
        return "MODELED" if self.funding_rate_per_bar is not None else "NOT_MODELED"


@dataclass(frozen=True)
class ExitProtectionConfig:
    """Research-only, causally observable stop protection configuration."""
    activation_r: float
    protected_stop_r: float = 0.0
    trailing_distance_r: float | None = None

    def __post_init__(self) -> None:
        if self.activation_r <= 0:
            raise ValueError("activation_r must be positive")
        if self.trailing_distance_r is not None and self.trailing_distance_r <= 0:
            raise ValueError("trailing_distance_r must be positive when supplied")


@dataclass(frozen=True)
class ReplayConfig:
    timeframe: str
    warmup_bars: int = 220
    entry_expiry_bars: int = 24
    max_holding_bars: int = 120
    entry_policy: EntryPolicy = EntryPolicy.LIMIT_AFTER_DECISION
    intrabar_policy: IntrabarPolicy = IntrabarPolicy.CONSERVATIVE_STOP_FIRST
    costs: ReplayCostModel = field(default_factory=ReplayCostModel)
    exit_protection: ExitProtectionConfig | None = None

    def __post_init__(self) -> None:
        if normalize_market_timeframe(self.timeframe) is None:
            raise ValueError("timeframe must be a supported canonical interval")
        if self.warmup_bars < 1 or self.entry_expiry_bars < 1 or self.max_holding_bars < 1:
            raise ValueError("warmup_bars, entry_expiry_bars, and max_holding_bars must be positive")


@dataclass(frozen=True)
class TemporalSplit:
    development_end: datetime
    test_start: datetime

    def __post_init__(self) -> None:
        if _as_utc(self.development_end) >= _as_utc(self.test_start):
            raise ValueError("development_end must precede test_start")

    def partition(self, records: Iterable[Mapping[str, Any]]) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
        development, test = [], []
        for record in sorted(records, key=lambda item: str(item.get("decision_at") or "")):
            value = _utc(record.get("decision_at"))
            if value is None:
                raise ValueError("every split record requires a UTC decision_at")
            if value <= _as_utc(self.development_end):
                development.append(record)
            elif value >= _as_utc(self.test_start):
                test.append(record)
        return development, test


@dataclass(frozen=True)
class WalkForwardWindow:
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime


def walk_forward_windows(
    start: datetime, end: datetime, *, train_bars: int, test_bars: int, timeframe: str,
) -> list[WalkForwardWindow]:
    """Create non-overlapping unseen test windows; configuration is frozen per fold."""
    seconds = timeframe_seconds(timeframe)
    if not seconds or train_bars < 1 or test_bars < 1:
        raise ValueError("supported timeframe and positive window sizes are required")
    cursor = _as_utc(start)
    finish = _as_utc(end)
    step = pd.Timedelta(seconds=seconds)
    windows: list[WalkForwardWindow] = []
    while cursor + step * (train_bars + test_bars) <= finish:
        train_end = cursor + step * train_bars
        test_start = train_end
        test_end = test_start + step * test_bars
        windows.append(WalkForwardWindow(cursor, train_end, test_start, test_end))
        cursor = test_end
    return windows


@dataclass(frozen=True)
class ReplayOutcome:
    signal_id: str
    strategy_version: str
    symbol: str
    timeframe: str
    direction: str
    decision_at: str
    entry_eligible_at: str
    fill_at: str | None
    exit_at: str | None
    intended_entry: float
    simulated_fill: float | None
    actual_fill: float | None
    stop: float
    targets: tuple[float, ...]
    exit_price: float | None
    exit_reason: str
    gross_pnl: float
    entry_fee: float
    exit_fee: float
    total_fee: float
    funding: float | None
    funding_status: str
    net_pnl: float
    gross_r: float
    net_r: float
    mfe_r: float | None
    mae_r: float | None
    mode: str = EvaluationMode.HISTORICAL_REPLAY.value
    ambiguity_flags: tuple[str, ...] = ()
    provenance: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _as_utc(value: datetime | pd.Timestamp) -> datetime:
    timestamp = pd.Timestamp(value)
    timestamp = timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp.tz_convert("UTC")
    return timestamp.to_pydatetime()


def _utc(value: Any) -> datetime | None:
    try:
        return _as_utc(value)
    except (TypeError, ValueError):
        return None


def _number(value: Any, name: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


class HistoricalReplayEngine:
    """Closed-candle replay with strict information and execution chronology."""

    def __init__(self, config: ReplayConfig):
        self.config = config
        self.integrity = DataIntegrityEngine()

    def prepare(self, candles: pd.DataFrame) -> pd.DataFrame:
        canonical = normalize_market_timeframe(self.config.timeframe)
        assert canonical
        # The final close is the evaluation reference, not today's wall clock.
        if "time" not in candles.columns:
            raise ValueError("historical replay requires UTC candle open timestamps")
        raw_times = pd.to_datetime(candles["time"], utc=True, errors="coerce")
        if raw_times.isna().any():
            reference = datetime.now(timezone.utc)
        else:
            seconds = timeframe_seconds(canonical)
            assert seconds
            reference = raw_times.max() + pd.Timedelta(seconds=seconds)
        result = self.integrity.prepare_market_frame(
            candles, timeframe=canonical, minimum_history=self.config.warmup_bars + 1,
            reference_time=reference, require_freshness=False, require_time_integrity=True,
        )
        if not result.valid:
            raise ValueError(f"historical candle contract failed: {result.status}/{result.code}")
        return result.frame.reset_index(drop=True)

    @staticmethod
    def _signal(raw: Mapping[str, Any]) -> dict[str, Any]:
        direction = str(raw.get("direction") or "").upper()
        if direction not in {"LONG", "SHORT"}:
            raise ValueError("direction must be LONG or SHORT")
        entry, stop = _number(raw["entry"], "entry"), _number(raw["stop"], "stop")
        targets = tuple(_number(value, "target") for value in (raw.get("targets") or [raw.get("tp1")]))
        if not targets or entry <= 0 or stop <= 0:
            raise ValueError("entry, stop, and at least one target are required")
        if direction == "LONG" and not (stop < entry < targets[0]):
            raise ValueError("invalid LONG geometry")
        if direction == "SHORT" and not (stop > entry > targets[0]):
            raise ValueError("invalid SHORT geometry")
        return {"direction": direction, "entry": entry, "stop": stop, "targets": targets,
                "strategy_version": str(raw.get("strategy_version") or "UNVERSIONED"),
                "signal_id": str(raw.get("signal_id") or "UNIDENTIFIED"),
                "symbol": str(raw.get("symbol") or "UNKNOWN"),
                "metadata": dict(raw.get("metadata") or {})}

    @staticmethod
    def _limit_fill(row: pd.Series, direction: str, intended: float) -> float | None:
        low, high, opened = float(row["low"]), float(row["high"]), float(row["open"])
        if not low <= intended <= high:
            return None
        # A limit may improve on a favorable gap; it may not fill at an earlier
        # candle price. The row is only considered after eligibility.
        return min(intended, opened) if direction == "LONG" else max(intended, opened)

    @staticmethod
    def _market_fill(row: pd.Series, direction: str, slippage: float) -> float:
        opened = float(row["open"])
        return opened * (1 + slippage) if direction == "LONG" else opened * (1 - slippage)

    @staticmethod
    def _barrier(row: pd.Series, direction: str, stop: float, target: float) -> tuple[str | None, float | None, bool]:
        opened, high, low = float(row["open"]), float(row["high"]), float(row["low"])
        if direction == "LONG":
            stop_hit, target_hit = low <= stop, high >= target
            if opened <= stop:
                return "STOP", opened, False
            if opened >= target:
                return "TP", opened, False
        else:
            stop_hit, target_hit = high >= stop, low <= target
            if opened >= stop:
                return "STOP", opened, False
            if opened <= target:
                return "TP", opened, False
        if stop_hit and target_hit:
            return "STOP", stop, True
        if stop_hit:
            return "STOP", stop, False
        if target_hit:
            return "TP", target, False
        return None, None, False

    def _simulate(
        self, frame: pd.DataFrame, index: int, raw: Mapping[str, Any], *, metadata: Mapping[str, Any],
    ) -> ReplayOutcome:
        signal = self._signal(raw)
        # A caller may attach decision-time fields to one signal without
        # replacing dataset/run identity supplied for the entire replay.
        metadata = {**dict(metadata), **signal["metadata"]}
        direction, intended, stop, target = signal["direction"], signal["entry"], signal["stop"], signal["targets"][0]
        try:
            entry_policy = EntryPolicy(str(raw.get("entry_policy") or self.config.entry_policy.value))
        except ValueError as exc:
            raise ValueError("unsupported replay entry policy") from exc
        seconds = timeframe_seconds(self.config.timeframe)
        assert seconds
        decision_at = _as_utc(frame.loc[index, "time"] + pd.Timedelta(seconds=seconds))
        eligible_index = index + 1
        eligible_at = _as_utc(frame.loc[eligible_index, "time"])
        expiry = min(len(frame) - 1, index + self.config.entry_expiry_bars)
        fill_index: int | None = None
        fill: float | None = None
        for candidate in range(eligible_index, expiry + 1):
            row = frame.iloc[candidate]
            if entry_policy == EntryPolicy.MARKET_NEXT_OPEN:
                fill = self._market_fill(row, direction, self.config.costs.market_slippage_rate)
                fill_index = candidate
                break
            fill = self._limit_fill(row, direction, intended)
            if fill is not None:
                fill_index = candidate
                break
        common = {"engine_version": REPLAY_ENGINE_VERSION,
                  "evaluation_mode": EvaluationMode.HISTORICAL_REPLAY.value,
                  "run_id": metadata.get("run_id"),
                  "dataset_id": metadata.get("dataset_id"),
                  "dataset_start": metadata.get("dataset_start"), "dataset_end": metadata.get("dataset_end"),
                  "data_provider": metadata.get("data_provider"), "data_quality": "VALID",
                  "decision_source": metadata.get("decision_source"),
                  "decision_authority": metadata.get("decision_authority"),
                  "decision_version": metadata.get("decision_version"),
                  "sample_role": metadata.get("sample_role"), "split_id": metadata.get("split_id"),
                  "evaluation_window": metadata.get("evaluation_window"),
                  "variants_evaluated": metadata.get("variants_evaluated"),
                  "market_regime": metadata.get("market_regime"), "confidence": metadata.get("confidence"),
                  "probability": metadata.get("probability"), "rr": metadata.get("rr"),
                  "evaluation_config": self._config_identity(), "cost_source": self.config.costs.source,
                  "fee_rate": self.config.costs.fee_rate,
                  "market_slippage_rate": self.config.costs.market_slippage_rate,
                  "funding_rate_per_bar": self.config.costs.funding_rate_per_bar,
                  "entry_policy": entry_policy.value,
                  "intrabar_policy": self.config.intrabar_policy.value,
                  "exit_protection": (asdict(self.config.exit_protection)
                                      if self.config.exit_protection is not None else None)}
        if fill_index is None or fill is None:
            return ReplayOutcome(signal["signal_id"], signal["strategy_version"], signal["symbol"], self.config.timeframe,
                direction, decision_at.isoformat(), eligible_at.isoformat(), None, None, intended, None, None, stop,
                signal["targets"], None, "ENTRY_EXPIRED", 0.0, 0.0, 0.0, 0.0, None,
                self.config.costs.funding_status, 0.0, 0.0, 0.0, None, None,
                ambiguity_flags=(), provenance=common)

        risk = abs(intended - stop)
        flags: list[str] = []
        # A limit's intrabar fill path is unknowable: exclude pre-fill movement
        # from MFE/MAE and do not award a same-bar target. A same-bar stop is
        # treated conservatively as reachable after fill.
        start = fill_index if entry_policy == EntryPolicy.MARKET_NEXT_OPEN else fill_index + 1
        if entry_policy == EntryPolicy.LIMIT_AFTER_DECISION:
            flags.append("LIMIT_ENTRY_BAR_PATH_UNOBSERVED")
            entry_row = frame.iloc[fill_index]
            stop_hit = (float(entry_row["low"]) <= stop if direction == "LONG" else float(entry_row["high"]) >= stop)
            if stop_hit:
                flags.append("LIMIT_ENTRY_BAR_STOP_CONSERVATIVE")
                start = fill_index
        exit_index = min(len(frame) - 1, fill_index + self.config.max_holding_bars)
        exit_reason, exit_price = "TIMEOUT", float(frame.iloc[exit_index]["close"])
        mfe = mae = 0.0
        active_stop = stop
        protected = False
        favorable_peak = 0.0
        for candidate in range(start, exit_index + 1):
            row = frame.iloc[candidate]
            event, price, ambiguous = self._barrier(row, direction, active_stop, target)
            if entry_policy == EntryPolicy.LIMIT_AFTER_DECISION and candidate == fill_index and event == "TP":
                event = None
            if event:
                # OHLC cannot place extrema before/after an exit barrier. Do not
                # report post-exit or ambiguous same-bar movement as MFE/MAE.
                exit_reason, exit_price, exit_index = event, float(price), candidate
                if event == "STOP" and protected:
                    exit_reason = "PROTECTED_STOP"
                if ambiguous:
                    flags.append("STOP_TARGET_SAME_CANDLE_CONSERVATIVE_STOP_FIRST")
                break
            if candidate != fill_index or entry_policy == EntryPolicy.MARKET_NEXT_OPEN:
                favorable = (float(row["high"]) - fill) if direction == "LONG" else (fill - float(row["low"]))
                adverse = (fill - float(row["low"])) if direction == "LONG" else (float(row["high"]) - fill)
                mfe, mae = max(mfe, favorable / risk), max(mae, adverse / risk)
                favorable_peak = max(favorable_peak, favorable / risk)
                protection = self.config.exit_protection
                # Protection takes effect only on the next candle. A bar's high
                # and low cannot justify a same-bar stop change under OHLC data.
                if protection is not None and favorable_peak >= protection.activation_r:
                    protected = True
                    locked_r = protection.protected_stop_r
                    if protection.trailing_distance_r is not None:
                        locked_r = max(locked_r, favorable_peak - protection.trailing_distance_r)
                    candidate_stop = (fill + locked_r * risk if direction == "LONG"
                                      else fill - locked_r * risk)
                    active_stop = max(active_stop, candidate_stop) if direction == "LONG" else min(active_stop, candidate_stop)
        gross = ((exit_price - fill) if direction == "LONG" else (fill - exit_price))
        entry_fee = abs(fill) * self.config.costs.fee_rate
        exit_fee = abs(exit_price) * self.config.costs.fee_rate
        bars_held = exit_index - fill_index + 1
        funding = (abs(fill) * self.config.costs.funding_rate_per_bar * bars_held
                   if self.config.costs.funding_rate_per_bar is not None else None)
        net = gross - entry_fee - exit_fee - (funding or 0.0)
        common["bars_held"] = bars_held
        return ReplayOutcome(signal["signal_id"], signal["strategy_version"], signal["symbol"], self.config.timeframe,
            direction, decision_at.isoformat(), eligible_at.isoformat(), _as_utc(frame.loc[fill_index, "time"]).isoformat(),
            _as_utc(frame.loc[exit_index, "time"] + pd.Timedelta(seconds=seconds)).isoformat(), intended, round(fill, 12), None,
            stop, signal["targets"], round(exit_price, 12), exit_reason, round(gross, 12), round(entry_fee, 12),
            round(exit_fee, 12), round(entry_fee + exit_fee, 12), None if funding is None else round(funding, 12),
            self.config.costs.funding_status, round(net, 12), round(gross / risk, 12), round(net / risk, 12),
            round(mfe, 12), round(mae, 12), ambiguity_flags=tuple(flags), provenance=common)

    def run(
        self, candles: pd.DataFrame, decision_factory: Callable[[pd.DataFrame], Mapping[str, Any] | None], *,
        metadata: Mapping[str, Any] | None = None,
    ) -> list[ReplayOutcome]:
        frame = self.prepare(candles)
        outcomes: list[ReplayOutcome] = []
        supplied = dict(metadata or {})
        supplied.update({
            "dataset_id": self._dataset_identity(frame),
            "dataset_start": _as_utc(frame.iloc[0]["time"]).isoformat(),
            "dataset_end": _as_utc(frame.iloc[-1]["time"]).isoformat(),
        })
        run_payload = {"engine": REPLAY_ENGINE_VERSION, "config": self._config_identity(), **supplied}
        supplied["run_id"] = hashlib.sha256(
            json.dumps(run_payload, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]
        for index in range(self.config.warmup_bars - 1, len(frame) - 1):
            # Closed current candle only; future rows are intentionally absent.
            decision = decision_factory(frame.iloc[: index + 1].copy())
            if decision:
                outcomes.append(self._simulate(frame, index, decision, metadata=supplied))
        return outcomes

    def run_plans(
        self, candles: pd.DataFrame, plans_by_decision_at: Mapping[str, Mapping[str, Any]], *,
        metadata: Mapping[str, Any] | None = None,
    ) -> list[ReplayOutcome]:
        """Replay an immutable sparse set of already-created decision plans.

        ``run`` must materialize every expanding prefix because an arbitrary
        decision factory may inspect any historical row.  Confirmation and
        cost-stress passes already possess frozen decision-time plans keyed by
        their canonical decision timestamp.  Rebuilding thousands of unused
        prefixes for those sparse passes is pure allocation overhead, so this
        method performs the same preparation, run identity, chronological
        lookup, and ``_simulate`` call without constructing unused snapshots.
        """
        frame = self.prepare(candles)
        supplied = dict(metadata or {})
        supplied.update({
            "dataset_id": self._dataset_identity(frame),
            "dataset_start": _as_utc(frame.iloc[0]["time"]).isoformat(),
            "dataset_end": _as_utc(frame.iloc[-1]["time"]).isoformat(),
        })
        run_payload = {"engine": REPLAY_ENGINE_VERSION, "config": self._config_identity(), **supplied}
        supplied["run_id"] = hashlib.sha256(
            json.dumps(run_payload, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]
        if not plans_by_decision_at:
            return []
        seconds = timeframe_seconds(self.config.timeframe)
        assert seconds
        # Resolve only declared plans to their candle index.  Iterating every
        # candle would preserve results but defeats the purpose of a sparse
        # replay when a confirmation family admits very few decisions.
        index_by_open_ns = {
            int(timestamp.value): index
            for index, timestamp in enumerate(pd.to_datetime(frame["time"], utc=True))
        }
        scheduled: list[tuple[int, Mapping[str, Any]]] = []
        delta = pd.Timedelta(seconds=seconds)
        for decision_at, decision in plans_by_decision_at.items():
            timestamp = pd.Timestamp(decision_at)
            timestamp = timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp.tz_convert("UTC")
            index = index_by_open_ns.get(int((timestamp - delta).value))
            if index is not None and self.config.warmup_bars - 1 <= index < len(frame) - 1:
                scheduled.append((index, decision))
        outcomes: list[ReplayOutcome] = []
        for index, decision in sorted(scheduled, key=lambda item: item[0]):
            outcomes.append(self._simulate(frame, index, decision, metadata=supplied))
        return outcomes

    def _config_identity(self) -> str:
        data = {"engine": REPLAY_ENGINE_VERSION, "config": asdict(self.config)}
        return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:16]

    @staticmethod
    def _dataset_identity(frame: pd.DataFrame) -> str:
        columns = ["time", "open", "high", "low", "close", "volume"]
        payload = frame.loc[:, columns].to_csv(index=False, date_format="%Y-%m-%dT%H:%M:%S.%f%z")
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


class PerformanceAttribution:
    """Reconciled metrics over one explicit evaluation mode only."""

    DIMENSIONS = (
        "strategy_version", "symbol", "timeframe", "direction", "exit_reason", "market_regime",
        "confidence_bucket", "probability_bucket", "rr_bucket", "hour", "session", "data_quality",
        "data_provider", "decision_source",
    )

    @staticmethod
    def metrics(outcomes: Iterable[ReplayOutcome]) -> dict[str, Any]:
        items = list(outcomes)
        for item in items:
            evidence_mode = str(item.provenance.get("evaluation_mode") or item.mode)
            if evidence_mode != item.mode:
                raise ValueError("evaluation mode conflicts with outcome provenance")
            if item.mode == EvaluationMode.LIVE.value:
                if item.actual_fill is None or item.provenance.get("execution_evidence") != "EXCHANGE_ACTUAL":
                    raise ValueError("LIVE metrics require actual exchange-fill provenance")
            elif item.actual_fill is not None:
                raise ValueError("actual fills cannot be attributed to a non-LIVE mode")
        modes = {item.mode for item in items}
        if len(modes) > 1:
            raise ValueError("performance modes must not be mixed")
        executed = [item for item in items if item.simulated_fill is not None]
        values = [item.net_pnl for item in executed]
        gross = [item.gross_pnl for item in executed]
        wins, losses, breakevens = [x for x in values if x > 0], [x for x in values if x < 0], [x for x in values if x == 0]
        equity = peak = max_drawdown = 0.0
        consecutive_wins = consecutive_losses = max_wins = max_losses = 0
        for value in values:
            equity += value
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, peak - equity)
            consecutive_wins = consecutive_wins + 1 if value > 0 else 0
            consecutive_losses = consecutive_losses + 1 if value < 0 else 0
            max_wins, max_losses = max(max_wins, consecutive_wins), max(max_losses, consecutive_losses)
        gross_profit, gross_loss = sum(x for x in values if x > 0), abs(sum(x for x in values if x < 0))
        timestamps = [_utc(item.decision_at) for item in items]
        timestamps = [value for value in timestamps if value is not None]
        cost_assumptions = {
            json.dumps({
                "source": item.provenance.get("cost_source"),
                "fee_rate": item.provenance.get("fee_rate"),
                "market_slippage_rate": item.provenance.get("market_slippage_rate"),
                "funding_rate_per_bar": item.provenance.get("funding_rate_per_bar"),
            }, sort_keys=True)
            for item in items
        }
        return {"evaluation_mode": next(iter(modes), EvaluationMode.HISTORICAL_REPLAY.value),
                "sample_size": len(executed), "trade_count": len(executed),
                "date_range": {
                    "start": min(timestamps).isoformat() if timestamps else None,
                    "end": max(timestamps).isoformat() if timestamps else None,
                },
                "dataset_ids": sorted({str(item.provenance.get("dataset_id")) for item in items
                                       if item.provenance.get("dataset_id")}),
                "cost_assumptions": [json.loads(value) for value in sorted(cost_assumptions)],
                "wins": len(wins), "losses": len(losses), "breakevens": len(breakevens),
                "win_rate": round(len(wins) / len(executed) * 100, 6) if executed else 0.0,
                "gross_pnl": round(sum(gross), 12), "net_pnl": round(sum(values), 12),
                "total_fees": round(sum(item.total_fee for item in executed), 12),
                "expectancy": round(sum(values) / len(executed), 12) if executed else 0.0,
                "expectancy_r": round(sum(item.net_r for item in executed) / len(executed), 12) if executed else 0.0,
                "average_win": round(sum(wins) / len(wins), 12) if wins else 0.0,
                "average_loss": round(sum(losses) / len(losses), 12) if losses else 0.0,
                "payoff_ratio": round(abs(sum(wins) / len(wins)) / abs(sum(losses) / len(losses)), 12) if wins and losses else None,
                "profit_factor": round(gross_profit / gross_loss, 12) if gross_loss else None,
                "max_drawdown": round(max_drawdown, 12), "max_consecutive_wins": max_wins,
                "max_consecutive_losses": max_losses, "average_mfe_r": round(sum(item.mfe_r or 0 for item in executed) / len(executed), 12) if executed else None,
                "average_mae_r": round(sum(item.mae_r or 0 for item in executed) / len(executed), 12) if executed else None,
                "funding_statuses": sorted({item.funding_status for item in executed})}

    @classmethod
    def group(cls, outcomes: Iterable[ReplayOutcome], dimension: str) -> dict[str, dict[str, Any]]:
        if dimension not in cls.DIMENSIONS:
            raise ValueError(f"unsupported attribution dimension: {dimension}")
        groups: dict[str, list[ReplayOutcome]] = {}
        for outcome in outcomes:
            groups.setdefault(cls._dimension(outcome, dimension), []).append(outcome)
        return {key: cls.metrics(value) for key, value in sorted(groups.items())}

    @staticmethod
    def _dimension(outcome: ReplayOutcome, dimension: str) -> str:
        if hasattr(outcome, dimension):
            return str(getattr(outcome, dimension) or "UNKNOWN")
        value = outcome.provenance.get(dimension)
        if dimension in {"confidence_bucket", "probability_bucket"}:
            try:
                floor = int(float(outcome.provenance.get(dimension.removesuffix("_bucket")) or 0) // 10 * 10)
                return f"{floor:02d}-{floor + 9:02d}"
            except (TypeError, ValueError):
                return "UNKNOWN"
        if dimension == "rr_bucket":
            try:
                return f"{int(float(outcome.provenance.get('rr') or 0))}R"
            except (TypeError, ValueError):
                return "UNKNOWN"
        if dimension in {"hour", "session"}:
            decision = _utc(outcome.decision_at)
            if decision is None:
                return "UNKNOWN"
            if dimension == "hour":
                return f"{decision.hour:02d}:00Z"
            if 0 <= decision.hour < 8:
                return "ASIA_UTC"
            if 8 <= decision.hour < 13:
                return "EUROPE_UTC"
            if 13 <= decision.hour < 21:
                return "US_UTC"
            return "OFF_HOURS_UTC"
        return str(value or "UNKNOWN")
