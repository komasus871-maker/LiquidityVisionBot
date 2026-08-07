from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class AlphaMetrics:
    trades: int
    wins: int
    losses: int
    win_rate: float
    expectancy_r: float
    profit_factor: float | None
    max_drawdown_r: float
    longest_losing_streak: int
    average_mfe_pct: float
    average_mae_pct: float


class AlphaResearchEngine:
    """Compatibility exporter and performance summaries.

    Persisted v9.9.16 research rows are leakage-safe. Raw historical signal
    dictionaries remain supported for old callers but are explicitly labeled
    ``UNVERIFIED_LEGACY_ROW`` and must not be presented as a backtest.
    """

    CLOSED_RESULTS = {"TP3", "STOP", "BREAKEVEN", "INVALIDATED", "EXPIRED", "MANUAL_STOP"}

    @staticmethod
    def _json(value: Any, default: Any) -> Any:
        if isinstance(value, (dict, list)):
            return value
        try:
            return json.loads(value or "")
        except (TypeError, ValueError, json.JSONDecodeError):
            return default

    def feature_row(self, signal: dict[str, Any]) -> dict[str, Any]:
        snapshot = self._json(signal.get("snapshot_json"), {})
        outcome = self._json(signal.get("outcome_json"), {})
        if isinstance(snapshot, dict) and snapshot:
            features = snapshot.get("features") if isinstance(snapshot.get("features"), dict) else {}
            regimes = snapshot.get("regimes") if isinstance(snapshot.get("regimes"), list) else []
            take_profits = list(snapshot.get("take_profits") or ())[:3]
            take_profits += [None] * (3 - len(take_profits))
            normalized = self._json(signal.get("vector_json"), {})
            missing = self._json(signal.get("missing_features_json"), [])
            return {
                "signal_id": snapshot.get("signal_id"), "symbol": snapshot.get("symbol"),
                "timeframe": snapshot.get("timeframe"), "side": snapshot.get("side"),
                "setup_key": snapshot.get("setup_family"),
                "regime": snapshot.get("primary_regime") or (regimes[0] if regimes else "UNKNOWN"),
                "direction_score": features.get("direction_score"),
                "entry_quality": features.get("entry_quality"), "risk_quality": features.get("risk_quality"),
                "readiness": features.get("execution_readiness"), "decision_action": features.get("decision_action"),
                "trade_quality_stars": features.get("trade_quality_stars"),
                "entry": snapshot.get("entry"), "stop": snapshot.get("stop"),
                "tp1": take_profits[0], "tp2": take_profits[1], "tp3": take_profits[2],
                "rr": snapshot.get("rr"), "result": outcome.get("signal_result"),
                "realized_r": float(outcome.get("signal_r") or 0.0),
                "mfe_pct": float(outcome.get("mfe_pct") or 0.0),
                "mae_pct": float(outcome.get("mae_pct") or 0.0),
                "created_at": snapshot.get("decision_at"), "activated_at": outcome.get("activated_at"),
                "closed_at": outcome.get("closed_at"), "data_integrity_valid": True,
                "capture_quality": signal.get("capture_quality") or "UNKNOWN",
                "snapshot_safety": "IMMUTABLE_DECISION_SNAPSHOT",
                "normalized_feature_version": signal.get("normalized_feature_version"),
                "normalized_data_quality": signal.get("data_quality"),
                "missing_decision_features_json": json.dumps(missing, sort_keys=True),
                "decision_features_json": json.dumps(normalized or features, sort_keys=True, default=str),
                "later_outcome_json": json.dumps(outcome, sort_keys=True, default=str),
            }
        features = self._json(signal.get("features_json"), {})
        regime = features.get("market_regime") or features.get("regime") or {}
        if isinstance(regime, dict):
            regime = regime.get("code") or regime.get("label") or "UNKNOWN"
        return {
            "signal_id": signal.get("id"),
            "symbol": signal.get("symbol"),
            "timeframe": signal.get("timeframe"),
            "side": signal.get("side"),
            "setup_key": signal.get("setup_key"),
            "regime": regime or "UNKNOWN",
            "direction_score": features.get("direction_score"),
            "entry_quality": features.get("entry_quality"),
            "risk_quality": features.get("risk_quality"),
            "readiness": features.get("execution_readiness"),
            "decision_action": features.get("decision_action"),
            "trade_quality_stars": features.get("trade_quality_stars"),
            "entry": signal.get("entry"),
            "stop": signal.get("stop"),
            "tp1": signal.get("tp1"),
            "tp2": signal.get("tp2"),
            "tp3": signal.get("tp3"),
            "rr": signal.get("rr"),
            "result": signal.get("result") or signal.get("status"),
            "realized_r": float(signal.get("realized_r") or 0.0),
            "mfe_pct": float(signal.get("max_profit_pct") or 0.0),
            "mae_pct": float(signal.get("max_drawdown_pct") or 0.0),
            "created_at": signal.get("created_at"),
            "activated_at": signal.get("activated_at"),
            "closed_at": signal.get("closed_at"),
            "data_integrity_valid": (signal.get("result") != "DATA_INTEGRITY_REJECTED"),
            "capture_quality": "UNVERIFIED_LEGACY_ROW",
            "snapshot_safety": "NOT_VERIFIED_FUTURE_SAFE",
        }

    def dataset(self, signals: Iterable[dict[str, Any]], *, usable_only: bool = True) -> list[dict[str, Any]]:
        rows = [self.feature_row(signal) for signal in signals]
        if usable_only:
            rows = [row for row in rows if row["data_integrity_valid"] and row["result"] in self.CLOSED_RESULTS]
        return rows

    @staticmethod
    def metrics(rows: Iterable[dict[str, Any]]) -> AlphaMetrics:
        items = list(rows)
        rs = [float(row.get("realized_r") or 0.0) for row in items]
        wins = sum(r > 0 for r in rs)
        losses = sum(r < 0 for r in rs)
        gains = sum(r for r in rs if r > 0)
        pain = abs(sum(r for r in rs if r < 0))
        equity = peak = drawdown = 0.0
        losing_streak = max_losing_streak = 0
        for r in rs:
            equity += r
            peak = max(peak, equity)
            drawdown = max(drawdown, peak - equity)
            if r < 0:
                losing_streak += 1
                max_losing_streak = max(max_losing_streak, losing_streak)
            else:
                losing_streak = 0
        count = len(items)
        return AlphaMetrics(
            trades=count,
            wins=wins,
            losses=losses,
            win_rate=(wins / count * 100) if count else 0.0,
            expectancy_r=(sum(rs) / count) if count else 0.0,
            profit_factor=(gains / pain) if pain > 0 else (None if gains == 0 else float("inf")),
            max_drawdown_r=drawdown,
            longest_losing_streak=max_losing_streak,
            average_mfe_pct=(sum(float(x.get("mfe_pct") or 0) for x in items) / count) if count else 0.0,
            average_mae_pct=(sum(float(x.get("mae_pct") or 0) for x in items) / count) if count else 0.0,
        )

    def grouped_metrics(self, rows: Iterable[dict[str, Any]], key: str) -> dict[str, AlphaMetrics]:
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            groups.setdefault(str(row.get(key) or "UNKNOWN"), []).append(row)
        return {name: self.metrics(group) for name, group in groups.items()}

    @staticmethod
    def export_csv(rows: Iterable[dict[str, Any]], path: str | Path) -> Path:
        output = Path(path)
        data = list(rows)
        output.parent.mkdir(parents=True, exist_ok=True)
        fields = sorted({key for row in data for key in row})
        with output.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            writer.writerows(data)
        return output

    @staticmethod
    def export_jsonl(rows: Iterable[dict[str, Any]], path: str | Path) -> Path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        return output
