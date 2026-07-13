from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from math import sqrt
from typing import Any, Callable, Iterable, Mapping

import numpy as np
import pandas as pd


class IntrabarPolicy(str, Enum):
    """How to resolve candles that touch both a target and the stop.

    CONSERVATIVE assumes the adverse fill happened first. This deliberately
    avoids optimistic backtests when only OHLC data is available.
    """

    CONSERVATIVE = "conservative"
    OPTIMISTIC = "optimistic"


@dataclass(frozen=True)
class BacktestConfig:
    warmup_bars: int = 250
    entry_expiry_bars: int = 24
    max_holding_bars: int = 120
    fee_rate: float = 0.0005
    slippage_rate: float = 0.0003
    funding_rate_per_bar: float = 0.0
    tp_allocations: tuple[float, float, float] = (0.40, 0.35, 0.25)
    move_to_breakeven_after_tp1: bool = True
    intrabar_policy: IntrabarPolicy = IntrabarPolicy.CONSERVATIVE
    executable_categories: tuple[str, ...] = ("READY_NOW",)
    minimum_readiness: float = 70.0
    one_trade_at_a_time: bool = True

    def __post_init__(self) -> None:
        if self.warmup_bars < 20:
            raise ValueError("warmup_bars must be at least 20")
        if self.entry_expiry_bars < 1 or self.max_holding_bars < 1:
            raise ValueError("entry_expiry_bars and max_holding_bars must be positive")
        if any(x < 0 for x in (self.fee_rate, self.slippage_rate)):
            raise ValueError("fees and slippage cannot be negative")
        if len(self.tp_allocations) != 3 or not np.isclose(sum(self.tp_allocations), 1.0):
            raise ValueError("tp_allocations must contain three values summing to 1.0")


@dataclass
class TradeResult:
    signal_index: int
    entry_index: int | None
    exit_index: int | None
    direction: str
    status: str
    entry: float
    stop: float
    tp1: float
    tp2: float
    tp3: float
    exit_price: float | None
    gross_r: float
    net_r: float
    fees_r: float
    slippage_r: float
    funding_r: float
    mfe_r: float
    mae_r: float
    bars_waited: int
    bars_held: int
    readiness: float
    regime: str
    category: str
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BacktestReport:
    trades: list[TradeResult]
    signals_seen: int
    signals_rejected: int
    signals_expired: int
    metrics: dict[str, Any]
    by_regime: dict[str, dict[str, Any]]
    by_direction: dict[str, dict[str, Any]]
    equity_curve_r: list[float]

    def as_dict(self) -> dict[str, Any]:
        return {
            "trades": [trade.as_dict() for trade in self.trades],
            "signals_seen": self.signals_seen,
            "signals_rejected": self.signals_rejected,
            "signals_expired": self.signals_expired,
            "metrics": self.metrics,
            "by_regime": self.by_regime,
            "by_direction": self.by_direction,
            "equity_curve_r": self.equity_curve_r,
        }


class BacktestEngine:
    """Event-driven OHLC trade simulator without look-ahead.

    A strategy receives only candles available at the signal bar. The returned
    mapping should use Analyzer-compatible keys: direction, entry, stop, tp1,
    tp2, tp3, execution_readiness and opportunity_category.
    """

    REQUIRED_COLUMNS = ("open", "high", "low", "close")

    def __init__(self, config: BacktestConfig | None = None):
        self.config = config or BacktestConfig()

    @staticmethod
    def _number(signal: Mapping[str, Any], key: str) -> float:
        value = float(signal[key])
        if not np.isfinite(value):
            raise ValueError(f"signal field {key!r} is not finite")
        return value

    def _normalize_signal(self, signal: Mapping[str, Any]) -> dict[str, Any]:
        direction = str(signal.get("direction", "")).upper()
        if direction not in {"LONG", "SHORT"}:
            raise ValueError("direction must be LONG or SHORT")
        normalized = {
            "direction": direction,
            "entry": self._number(signal, "entry"),
            "stop": self._number(signal, "stop"),
            "tp1": self._number(signal, "tp1"),
            "tp2": self._number(signal, "tp2"),
            "tp3": self._number(signal, "tp3"),
            "readiness": float(signal.get("execution_readiness", signal.get("readiness", 0.0))),
            "category": str(signal.get("opportunity_category", signal.get("category", "UNKNOWN"))),
            "regime": str((signal.get("market_regime") or {}).get("code", signal.get("regime", "UNKNOWN"))),
            "metadata": {
                "direction_score": signal.get("direction_score"),
                "entry_quality": signal.get("entry_quality"),
                "risk_quality": signal.get("risk_quality"),
                "directional_edge": signal.get("directional_edge"),
                "ai_grade": signal.get("ai_grade"),
                "strongest_drivers": signal.get("strongest_drivers", []),
                "biggest_blockers": signal.get("biggest_blockers", []),
            },
        }
        if normalized["readiness"] < self.config.minimum_readiness:
            normalized["reject_reason"] = "readiness_below_threshold"
        elif normalized["category"] not in self.config.executable_categories:
            normalized["reject_reason"] = "category_not_executable"
        else:
            normalized["reject_reason"] = None

        risk = abs(normalized["entry"] - normalized["stop"])
        if risk <= 0:
            raise ValueError("entry and stop must differ")
        if direction == "LONG" and not (
            normalized["stop"] < normalized["entry"] < normalized["tp1"] <= normalized["tp2"] <= normalized["tp3"]
        ):
            raise ValueError("invalid LONG price geometry")
        if direction == "SHORT" and not (
            normalized["stop"] > normalized["entry"] > normalized["tp1"] >= normalized["tp2"] >= normalized["tp3"]
        ):
            raise ValueError("invalid SHORT price geometry")
        return normalized

    @staticmethod
    def _entry_touched(row: pd.Series, direction: str, entry: float) -> bool:
        return float(row["low"]) <= entry <= float(row["high"])

    @staticmethod
    def _adverse_favorable(row: pd.Series, direction: str, entry: float) -> tuple[float, float]:
        if direction == "LONG":
            adverse = max(0.0, entry - float(row["low"]))
            favorable = max(0.0, float(row["high"]) - entry)
        else:
            adverse = max(0.0, float(row["high"]) - entry)
            favorable = max(0.0, entry - float(row["low"]))
        return adverse, favorable

    @staticmethod
    def _hit(row: pd.Series, direction: str, price: float, target: bool) -> bool:
        if direction == "LONG":
            return float(row["high"] if target else row["low"]) >= price if target else float(row["low"]) <= price
        return float(row["low"]) <= price if target else float(row["high"]) >= price

    def _costs_r(self, entry: float, exit_notional_prices: Iterable[tuple[float, float]], risk: float, bars: int) -> tuple[float, float, float]:
        fee_cost = self.config.fee_rate * entry
        slip_cost = self.config.slippage_rate * entry
        for price, allocation in exit_notional_prices:
            fee_cost += self.config.fee_rate * price * allocation
            slip_cost += self.config.slippage_rate * price * allocation
        funding_cost = self.config.funding_rate_per_bar * entry * bars
        return fee_cost / risk, slip_cost / risk, funding_cost / risk

    def _simulate(self, candles: pd.DataFrame, signal_index: int, signal: dict[str, Any]) -> TradeResult:
        direction = signal["direction"]
        entry, stop = signal["entry"], signal["stop"]
        targets = (signal["tp1"], signal["tp2"], signal["tp3"])
        risk = abs(entry - stop)
        last_entry_bar = min(len(candles) - 1, signal_index + self.config.entry_expiry_bars)
        entry_index: int | None = None

        for idx in range(signal_index + 1, last_entry_bar + 1):
            if self._entry_touched(candles.iloc[idx], direction, entry):
                entry_index = idx
                break

        if entry_index is None:
            return TradeResult(
                signal_index, None, None, direction, "EXPIRED", entry, stop, *targets,
                None, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                last_entry_bar - signal_index, 0, signal["readiness"], signal["regime"],
                signal["category"], "entry_not_touched", signal["metadata"],
            )

        realized = 0.0
        remaining = 1.0
        hit_targets = [False, False, False]
        effective_stop = stop
        mfe = mae = 0.0
        exits: list[tuple[float, float]] = []
        end_index = min(len(candles) - 1, entry_index + self.config.max_holding_bars)
        exit_status = "TIMEOUT"
        exit_price = float(candles.iloc[end_index]["close"])

        for idx in range(entry_index, end_index + 1):
            row = candles.iloc[idx]
            adverse, favorable = self._adverse_favorable(row, direction, entry)
            mae = max(mae, adverse / risk)
            mfe = max(mfe, favorable / risk)

            stop_hit = self._hit(row, direction, effective_stop, target=False)
            next_target_idx = next((n for n, hit in enumerate(hit_targets) if not hit), None)
            target_hit = next_target_idx is not None and self._hit(row, direction, targets[next_target_idx], target=True)

            # OHLC cannot reveal path. Conservative policy resolves ambiguity
            # against the strategy, preventing inflated results.
            if stop_hit and target_hit and self.config.intrabar_policy == IntrabarPolicy.CONSERVATIVE:
                target_hit = False
            elif stop_hit and target_hit:
                stop_hit = False

            if stop_hit:
                pnl_per_unit = (effective_stop - entry) if direction == "LONG" else (entry - effective_stop)
                realized += remaining * pnl_per_unit / risk
                exits.append((effective_stop, remaining))
                exit_price = effective_stop
                exit_status = "BREAKEVEN" if np.isclose(effective_stop, entry) else "STOP"
                end_index = idx
                break

            if target_hit and next_target_idx is not None:
                allocation = min(self.config.tp_allocations[next_target_idx], remaining)
                price = targets[next_target_idx]
                pnl_per_unit = (price - entry) if direction == "LONG" else (entry - price)
                realized += allocation * pnl_per_unit / risk
                remaining -= allocation
                exits.append((price, allocation))
                hit_targets[next_target_idx] = True
                exit_price = price
                if next_target_idx == 0 and self.config.move_to_breakeven_after_tp1:
                    effective_stop = entry
                if next_target_idx == 2 or remaining <= 1e-12:
                    exit_status = "TP3"
                    end_index = idx
                    break
        else:
            end_index = min(end_index, len(candles) - 1)

        if exit_status == "TIMEOUT":
            close_price = float(candles.iloc[end_index]["close"])
            pnl_per_unit = (close_price - entry) if direction == "LONG" else (entry - close_price)
            realized += remaining * pnl_per_unit / risk
            exits.append((close_price, remaining))
            exit_price = close_price
            if hit_targets[1]:
                exit_status = "TP2_TIMEOUT"
            elif hit_targets[0]:
                exit_status = "TP1_TIMEOUT"

        bars_held = end_index - entry_index + 1
        fees_r, slippage_r, funding_r = self._costs_r(entry, exits, risk, bars_held)
        net = realized - fees_r - slippage_r - funding_r
        return TradeResult(
            signal_index=signal_index,
            entry_index=entry_index,
            exit_index=end_index,
            direction=direction,
            status=exit_status,
            entry=entry,
            stop=stop,
            tp1=targets[0],
            tp2=targets[1],
            tp3=targets[2],
            exit_price=exit_price,
            gross_r=round(realized, 6),
            net_r=round(net, 6),
            fees_r=round(fees_r, 6),
            slippage_r=round(slippage_r, 6),
            funding_r=round(funding_r, 6),
            mfe_r=round(mfe, 6),
            mae_r=round(mae, 6),
            bars_waited=entry_index - signal_index,
            bars_held=bars_held,
            readiness=signal["readiness"],
            regime=signal["regime"],
            category=signal["category"],
            reason="simulated_exit",
            metadata=signal["metadata"],
        )

    @staticmethod
    def _metrics(trades: list[TradeResult]) -> dict[str, Any]:
        executed = [t for t in trades if t.entry_index is not None]
        values = np.array([t.net_r for t in executed], dtype=float)
        if not len(values):
            return {
                "trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
                "expectancy_r": 0.0, "profit_factor": 0.0, "net_r": 0.0,
                "max_drawdown_r": 0.0, "longest_losing_streak": 0,
                "avg_win_r": 0.0, "avg_loss_r": 0.0, "sharpe_like": 0.0,
            }
        wins = values[values > 0]
        losses = values[values <= 0]
        gross_profit = float(wins.sum())
        gross_loss = abs(float(losses.sum()))
        equity = np.cumsum(values)
        peaks = np.maximum.accumulate(np.concatenate(([0.0], equity)))
        curve = np.concatenate(([0.0], equity))
        drawdown = peaks - curve
        streak = longest = 0
        for value in values:
            streak = streak + 1 if value <= 0 else 0
            longest = max(longest, streak)
        std = float(values.std(ddof=1)) if len(values) > 1 else 0.0
        sharpe_like = float(values.mean() / std * sqrt(len(values))) if std > 1e-12 else 0.0
        return {
            "trades": int(len(values)),
            "wins": int(len(wins)),
            "losses": int(len(losses)),
            "win_rate": round(float((values > 0).mean() * 100), 2),
            "expectancy_r": round(float(values.mean()), 4),
            "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else float("inf"),
            "net_r": round(float(values.sum()), 4),
            "max_drawdown_r": round(float(drawdown.max()), 4),
            "longest_losing_streak": int(longest),
            "avg_win_r": round(float(wins.mean()), 4) if len(wins) else 0.0,
            "avg_loss_r": round(float(losses.mean()), 4) if len(losses) else 0.0,
            "avg_mfe_r": round(float(np.mean([t.mfe_r for t in executed])), 4),
            "avg_mae_r": round(float(np.mean([t.mae_r for t in executed])), 4),
            "sharpe_like": round(sharpe_like, 4),
        }

    def run(self, candles: pd.DataFrame, strategy: Callable[[pd.DataFrame], Mapping[str, Any] | None]) -> BacktestReport:
        missing = [column for column in self.REQUIRED_COLUMNS if column not in candles.columns]
        if missing:
            raise ValueError(f"candles missing columns: {', '.join(missing)}")
        if len(candles) <= self.config.warmup_bars:
            raise ValueError("not enough candles for configured warmup")

        frame = candles.reset_index(drop=True).copy()
        trades: list[TradeResult] = []
        signals_seen = signals_rejected = signals_expired = 0
        next_available_index = self.config.warmup_bars - 1

        for signal_index in range(self.config.warmup_bars - 1, len(frame) - 1):
            if self.config.one_trade_at_a_time and signal_index < next_available_index:
                continue
            snapshot = frame.iloc[: signal_index + 1].copy()
            raw_signal = strategy(snapshot)
            if not raw_signal:
                continue
            signals_seen += 1
            try:
                signal = self._normalize_signal(raw_signal)
            except (KeyError, TypeError, ValueError):
                signals_rejected += 1
                continue
            if signal["reject_reason"]:
                signals_rejected += 1
                continue
            trade = self._simulate(frame, signal_index, signal)
            trades.append(trade)
            if trade.status == "EXPIRED":
                signals_expired += 1
            if self.config.one_trade_at_a_time and trade.exit_index is not None:
                next_available_index = trade.exit_index + 1

        executed = [t for t in trades if t.entry_index is not None]
        equity = np.cumsum([t.net_r for t in executed]).tolist() if executed else []

        def grouped(field: str) -> dict[str, dict[str, Any]]:
            output: dict[str, dict[str, Any]] = {}
            for value in sorted({getattr(t, field) for t in executed}):
                output[str(value)] = self._metrics([t for t in executed if getattr(t, field) == value])
            return output

        return BacktestReport(
            trades=trades,
            signals_seen=signals_seen,
            signals_rejected=signals_rejected,
            signals_expired=signals_expired,
            metrics=self._metrics(trades),
            by_regime=grouped("regime"),
            by_direction=grouped("direction"),
            equity_curve_r=[round(float(x), 6) for x in equity],
        )
