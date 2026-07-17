from __future__ import annotations

from dataclasses import asdict
from statistics import mean
from typing import Any

from domain.intelligence import TradeDNABuilder
from services.similarity_engine_v2 import SimilarityEngineV2
from services.trade_memory import TradeMemoryService


class IntelligenceLayer:
    """Single read model for historical probability, Trade DNA similarity and AI memory."""

    def __init__(self) -> None:
        self.similarity = SimilarityEngineV2()
        self.memory = TradeMemoryService()

    def build_for_signal(self, signal: dict[str, Any]) -> dict[str, Any]:
        signal_id = int(signal.get("id") or 0)
        dna = TradeDNABuilder.from_signal(signal)
        if isinstance(dna.extras, dict):
            dna.extras["signal_id"] = signal_id
        cases = self.similarity.find(dna=dna, limit=20, minimum_similarity=25.0)
        similarity = self.similarity.summarize(cases)
        memory = self.memory.get(signal_id)
        if not memory and signal.get("closed_at"):
            memory = self.memory.create_for_signal(signal_id)

        completed = [case for case in cases if case.status in {"TP1", "TP2", "TP3", "STOP", "BREAKEVEN", "INVALIDATED", "EXPIRED", "MANUAL_STOP"}]
        positive = sum(case.realized_r > 0 for case in completed)
        sample_count = len(completed)
        historical = {
            "samples": sample_count,
            "win_rate": round(positive / sample_count * 100, 1) if sample_count else 0.0,
            "expected_r": similarity.get("expected_r", 0.0),
            "average_similarity": similarity.get("average_similarity", 0.0),
            "reliability": similarity.get("reliability", "Insufficient"),
            "avg_mfe": round(mean([case.mfe for case in completed]), 2) if completed else 0.0,
            "avg_mae": round(mean([case.mae for case in completed]), 2) if completed else 0.0,
        }
        return {
            "dna": dna.to_dict(),
            "memory": memory or {},
            "similarity": similarity,
            "historical": historical,
            "similar_trades": [asdict(case) for case in cases[:5]],
        }
