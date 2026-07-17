from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class MarketMemory:
    """Small persistent time-series memory for analysis evolution.

    JSONL is intentionally used instead of a schema migration so the feature is
    safe on both SQLite and hosted PostgreSQL deployments. Writes are append-only
    and failures never block analysis delivery.
    """

    def __init__(self, path: str | None = None, max_rows: int = 5000):
        data_dir = Path(os.getenv("DATA_DIR", "data"))
        self.path = Path(path) if path else data_dir / "market_memory.jsonl"
        self.max_rows = max(200, int(max_rows))
        self._lock = threading.RLock()

    @staticmethod
    def _snapshot(symbol: str, timeframe: str, analysis: dict[str, Any]) -> dict[str, Any]:
        return {
            "at": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol.upper(),
            "timeframe": timeframe.lower(),
            "price": float(analysis.get("price") or 0),
            "direction": str(analysis.get("direction") or ""),
            "direction_score": float(analysis.get("direction_score") or 0),
            "readiness": float(analysis.get("execution_readiness") or 0),
            "entry_quality": float(analysis.get("entry_quality") or 0),
            "decision_score": float((analysis.get("unified_decision") or {}).get("score") or 0),
            "decision": str((analysis.get("unified_decision") or {}).get("action") or ""),
            "volume_ratio": float(analysis.get("volume_ratio") or 0),
            "rsi": float(analysis.get("rsi") or 0),
            "trend": str(analysis.get("trend") or ""),
            "structure": str(analysis.get("structure") or ""),
            "displacement": str(analysis.get("displacement") or ""),
        }

    def _read(self, symbol: str, timeframe: str, limit: int = 12) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if row.get("symbol") == symbol.upper() and row.get("timeframe") == timeframe.lower():
                        rows.append(row)
        except OSError:
            return []
        return rows[-limit:]

    def remember(self, symbol: str, timeframe: str, analysis: dict[str, Any]) -> dict[str, Any]:
        current = self._snapshot(symbol, timeframe, analysis)
        with self._lock:
            history = self._read(symbol, timeframe, 12)
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(current, ensure_ascii=False, separators=(",", ":")) + "\n")
            except OSError:
                pass
        series = history + [current]
        return self.summarize(series)

    @staticmethod
    def summarize(series: list[dict[str, Any]]) -> dict[str, Any]:
        if len(series) < 2:
            return {"samples": len(series), "state": "LEARNING", "summary": "Market memory has started collecting snapshots.", "changes": []}
        first, last = series[0], series[-1]
        changes = []
        for key, label in (("direction_score", "Direction"), ("readiness", "Readiness"), ("decision_score", "Decision"), ("volume_ratio", "Volume")):
            delta = float(last.get(key) or 0) - float(first.get(key) or 0)
            threshold = 0.08 if key == "volume_ratio" else 3.0
            if abs(delta) >= threshold:
                suffix = "x" if key == "volume_ratio" else " pts"
                changes.append(f"{label} {'strengthened' if delta > 0 else 'weakened'} by {abs(delta):.1f}{suffix}")
        flips = sum(1 for a, b in zip(series, series[1:]) if a.get("direction") != b.get("direction"))
        if flips >= 2:
            changes.append(f"Directional bias flipped {flips} times")
        state = "IMPROVING" if float(last.get("decision_score") or 0) > float(first.get("decision_score") or 0) + 4 else "DETERIORATING" if float(last.get("decision_score") or 0) < float(first.get("decision_score") or 0) - 4 else "STABLE"
        summary = changes[0] if changes else "No material change across recent snapshots."
        window = series[-min(len(series), 18):]
        avg_direction = sum(float(x.get("direction_score") or 0) for x in window) / len(window)
        avg_readiness = sum(float(x.get("readiness") or 0) for x in window) / len(window)
        avg_decision = sum(float(x.get("decision_score") or 0) for x in window) / len(window)
        direction_delta = float(last.get("direction_score") or 0) - float(first.get("direction_score") or 0)
        readiness_delta = float(last.get("readiness") or 0) - float(first.get("readiness") or 0)
        trend_state = "IMPROVING" if direction_delta > 4 else "WEAKENING" if direction_delta < -4 else "STABLE"
        execution_state = "IMPROVING" if readiness_delta > 4 else "DETERIORATING" if readiness_delta < -4 else "STABLE"
        return {
            "samples": len(series), "state": state, "summary": summary, "changes": changes[:4],
            "first_at": first.get("at"), "last_at": last.get("at"),
            "averages": {"direction": round(avg_direction, 1), "readiness": round(avg_readiness, 1), "decision": round(avg_decision, 1)},
            "trend_state": trend_state, "execution_state": execution_state,
            "direction_delta": round(direction_delta, 1), "readiness_delta": round(readiness_delta, 1),
            "window": len(window),
        }
