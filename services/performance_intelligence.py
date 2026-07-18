from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from database.database import connect

ACTIVE = {"ACTIVE", "TP1", "TP2"}
RESOLVED = {"TP3", "STOP", "BREAKEVEN", "MANUAL_STOP", "INVALIDATED"}


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _resolved(row: dict[str, Any]) -> bool:
    if not row.get("activated_at") or not row.get("closed_at"):
        return False
    status = str(row.get("status") or "")
    if status not in RESOLVED:
        return False
    if status in {"MANUAL_STOP", "INVALIDATED"} and row.get("realized_r") is None:
        return False
    return row.get("realized_r") is not None


@dataclass(frozen=True)
class Segment:
    name: str
    trades: int
    wins: int
    total_r: float

    @property
    def win_rate(self) -> float:
        return round(self.wins / self.trades * 100, 2) if self.trades else 0.0

    @property
    def expectancy(self) -> float:
        return round(self.total_r / self.trades, 2) if self.trades else 0.0


class PerformanceIntelligence:
    """Portfolio, performance and Trade-DNA analytics over persisted lifecycle data."""

    def rows(self, owner_id: int) -> list[dict[str, Any]]:
        with connect() as conn:
            rows = conn.execute(
                "SELECT * FROM signals WHERE owner_telegram_id=? ORDER BY id ASC", (owner_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _segments(rows: Iterable[dict[str, Any]], field: str) -> list[Segment]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[str(row.get(field) or "UNKNOWN")].append(row)
        result = []
        for name, items in grouped.items():
            total_r = sum(_f(x.get("realized_r")) for x in items)
            wins = sum(1 for x in items if _f(x.get("realized_r")) > 0)
            result.append(Segment(name=name, trades=len(items), wins=wins, total_r=round(total_r, 2)))
        return sorted(result, key=lambda x: (x.expectancy, x.trades), reverse=True)

    def performance(self, owner_id: int) -> dict[str, Any]:
        rows = self.rows(owner_id)
        closed = [x for x in rows if _resolved(x)]
        values = [_f(x.get("realized_r")) for x in closed]
        wins = [x for x in values if x > 0]
        losses = [x for x in values if x < 0]
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        durations = []
        for row in closed:
            start, end = _parse(row.get("activated_at")), _parse(row.get("closed_at"))
            if start and end:
                durations.append(max(0.0, (end - start).total_seconds() / 3600))
        current_streak = 0
        streak_type = "—"
        for value in reversed(values):
            kind = "WIN" if value > 0 else "LOSS" if value < 0 else "BE"
            if streak_type in {"—", kind}:
                streak_type = kind
                current_streak += 1
            else:
                break
        return {
            "trades": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / (len(wins) + len(losses)) * 100, 2) if wins or losses else 0.0,
            "net_r": round(sum(values), 2),
            "expectancy": round(sum(values) / len(values), 2) if values else 0.0,
            "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss else (999.0 if gross_profit else 0.0),
            "avg_win": round(gross_profit / len(wins), 2) if wins else 0.0,
            "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
            "avg_hold_hours": round(sum(durations) / len(durations), 2) if durations else 0.0,
            "streak": current_streak,
            "streak_type": streak_type,
            "symbols": self._segments(closed, "symbol"),
            "timeframes": self._segments(closed, "timeframe"),
            "sides": self._segments(closed, "side"),
        }

    def portfolio(self, owner_id: int) -> dict[str, Any]:
        active = [x for x in self.rows(owner_id) if str(x.get("status")) in ACTIVE]
        longs = sum(1 for x in active if str(x.get("side")) == "LONG")
        shorts = len(active) - longs
        open_r = 0.0
        risk_r = 0.0
        symbols: dict[str, int] = defaultdict(int)
        for row in active:
            entry = _f(row.get("entry"))
            stop = _f(row.get("stop"))
            current = _f(row.get("current_price"), entry)
            side = str(row.get("side") or "LONG")
            unit = abs(entry - stop)
            if unit > 0:
                open_r += ((current - entry) / unit) * (1 if side == "LONG" else -1)
                risk_r += 1.0
            symbols[str(row.get("symbol") or "UNKNOWN")] += 1
        dominant = "LONG" if longs > shorts else "SHORT" if shorts > longs else "BALANCED"
        concentration = max(symbols.values(), default=0)
        heat = "LOW" if risk_r <= 2 else "MEDIUM" if risk_r <= 5 else "HIGH"
        warnings = []
        if len(active) >= 3 and max(longs, shorts) / len(active) >= 0.75:
            warnings.append(f"Directional concentration: {max(longs, shorts)}/{len(active)} positions are {dominant}")
        if concentration >= 2:
            symbol = max(symbols, key=symbols.get)
            warnings.append(f"Symbol concentration: {symbols[symbol]} simultaneous {symbol} positions")
        if risk_r > 5:
            warnings.append("Portfolio heat is high; reduce total exposure or protect open risk")
        return {
            "active": active,
            "count": len(active),
            "longs": longs,
            "shorts": shorts,
            "dominant": dominant,
            "open_r": round(open_r, 2),
            "risk_r": round(risk_r, 2),
            "heat": heat,
            "warnings": warnings,
        }

    def dna(self, owner_id: int) -> dict[str, Any]:
        report = self.performance(owner_id)
        def first(items: list[Segment]) -> Segment | None:
            qualified = [x for x in items if x.trades >= 2]
            return qualified[0] if qualified else (items[0] if items else None)
        best_symbol = first(report["symbols"])
        best_tf = first(report["timeframes"])
        best_side = first(report["sides"])
        worst_symbol = min(report["symbols"], key=lambda x: x.expectancy, default=None)
        strengths, weaknesses = [], []
        if report["expectancy"] > 0:
            strengths.append(f"Positive expectancy: {report['expectancy']:+.2f}R per resolved trade")
        if report["profit_factor"] >= 1.5:
            strengths.append(f"Healthy profit factor: {report['profit_factor']:.2f}")
        if report["win_rate"] < 45:
            weaknesses.append("Low resolved win rate; entries or invalidation logic need review")
        if report["avg_loss"] < -1.2:
            weaknesses.append("Average loss exceeds planned 1R; audit manual exits and slippage")
        return {
            **report,
            "best_symbol": best_symbol,
            "worst_symbol": worst_symbol,
            "best_timeframe": best_tf,
            "best_side": best_side,
            "strengths": strengths,
            "weaknesses": weaknesses,
            "sample_ready": report["trades"] >= 30,
        }
