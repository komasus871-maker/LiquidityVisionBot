from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from database.signal_history import SignalHistory


@dataclass(slots=True)
class ProbabilityStats:
    sample_size: int
    tp1_rate: float
    tp2_rate: float
    tp3_rate: float
    stop_rate: float
    avg_mfe: float
    avg_mae: float

    @property
    def reliable(self) -> bool:
        return self.sample_size >= 30


class ProbabilityFoundation:
    """Historical outcome statistics for the current setup fingerprint.

    This is deliberately statistical, not machine-learning based. It only
    reports observed outcomes and never fabricates a probability when the
    sample is too small.
    """

    def __init__(self, history: SignalHistory | None = None):
        self.history = history or SignalHistory()

    def for_setup(self, setup_key: str, timeframe: str, side: str) -> ProbabilityStats:
        raw = self.history.get_setup_stats(setup_key=setup_key, timeframe=timeframe, side=side)
        return ProbabilityStats(
            sample_size=int(raw.get("sample_size") or 0),
            tp1_rate=float(raw.get("tp1_rate") or 0),
            tp2_rate=float(raw.get("tp2_rate") or 0),
            tp3_rate=float(raw.get("tp3_rate") or 0),
            stop_rate=float(raw.get("stop_rate") or 0),
            avg_mfe=float(raw.get("avg_mfe") or 0),
            avg_mae=float(raw.get("avg_mae") or 0),
        )

    @staticmethod
    def as_dict(stats: ProbabilityStats) -> dict[str, Any]:
        return {
            "sample_size": stats.sample_size,
            "tp1_rate": round(stats.tp1_rate, 1),
            "tp2_rate": round(stats.tp2_rate, 1),
            "tp3_rate": round(stats.tp3_rate, 1),
            "stop_rate": round(stats.stop_rate, 1),
            "avg_mfe": round(stats.avg_mfe, 2),
            "avg_mae": round(stats.avg_mae, 2),
            "reliable": stats.reliable,
        }
