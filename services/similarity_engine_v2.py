from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from typing import Any

from database.database import connect
from domain.intelligence import TradeDNA, TradeDNABuilder


@dataclass(slots=True)
class SimilarTrade:
    signal_id: int
    symbol: str
    timeframe: str
    side: str
    status: str
    similarity: float
    realized_r: float
    mfe: float
    mae: float
    matching_reasons: list[str]
    difference_reasons: list[str]


class SimilarityEngineV2:
    """Weighted mixed-type similarity over canonical TradeDNA snapshots."""

    CATEGORICAL = {
        "trend": 1.3, "structure": 1.6, "bos": 1.1, "choch": 1.2, "liquidity_event": 1.3,
        "sweep": 1.4, "order_block": 1.1, "breaker": .8, "mitigation": .8, "fvg": 1.0,
        "premium_discount": 1.2, "session": .5, "market_regime": 1.5, "htf_alignment": 1.5,
        "macd": .5, "volume": .7, "displacement": 1.0, "execution_status": .7,
        "opportunity_category": .6, "decision_action": .8, "ai_grade": .5,
    }
    NUMERIC = {
        "rsi": (15.0, .7), "ema50_distance_pct": (2.5, .8), "ema200_distance_pct": (5.0, .8),
        "atr_pct": (1.5, .8), "risk_pct": (1.5, 1.0), "rr": (1.5, 1.2),
        "confidence": (25.0, .8), "direction_score": (25.0, 1.2), "entry_quality": (25.0, 1.1),
        "risk_quality": (25.0, 1.0), "readiness": (25.0, 1.0), "trade_health": (25.0, .5),
    }

    @staticmethod
    def _norm(value: Any) -> str:
        return " ".join(str(value or "UNKNOWN").lower().replace("✅", "").split())

    @classmethod
    def compare(cls, left: TradeDNA, right: TradeDNA) -> tuple[float, list[str], list[str]]:
        score = total = 0.0
        matches: list[tuple[float, str]] = []
        differences: list[tuple[float, str]] = []
        for field, weight in cls.CATEGORICAL.items():
            a, b = cls._norm(getattr(left, field)), cls._norm(getattr(right, field))
            if a == "unknown" and b == "unknown":
                continue
            total += weight
            if a == b:
                score += weight
                matches.append((weight, f"Same {field.replace('_', ' ')}: {getattr(left, field)}"))
            else:
                # Partial token overlap rewards semantically close labels.
                ta, tb = set(a.split()), set(b.split())
                overlap = len(ta & tb) / max(1, len(ta | tb))
                score += weight * overlap * .6
                differences.append((weight * (1-overlap), f"{field.replace('_', ' ').title()}: {getattr(left, field)} vs {getattr(right, field)}"))
        for field, (scale, weight) in cls.NUMERIC.items():
            a, b = float(getattr(left, field) or 0), float(getattr(right, field) or 0)
            total += weight
            closeness = math.exp(-abs(a-b) / max(scale, 1e-9))
            score += weight * closeness
            if closeness >= .8:
                matches.append((weight * closeness, f"Similar {field.replace('_', ' ')} ({a:.1f} vs {b:.1f})"))
            elif closeness < .45:
                differences.append((weight * (1-closeness), f"Different {field.replace('_', ' ')} ({a:.1f} vs {b:.1f})"))
        if left.side == right.side:
            score += 2.0; total += 2.0; matches.append((2.0, f"Same direction: {left.side}"))
        else:
            total += 2.0; differences.append((2.0, f"Opposite direction: {left.side} vs {right.side}"))
        similarity = score / total * 100 if total else 0.0
        return round(similarity, 1), [x[1] for x in sorted(matches, reverse=True)[:5]], [x[1] for x in sorted(differences, reverse=True)[:5]]

    def find(self, *, dna: TradeDNA, limit: int = 20, minimum_similarity: float = 35.0) -> list[SimilarTrade]:
        with connect() as conn:
            rows = conn.execute("""
                SELECT id,symbol,timeframe,side,status,trade_dna_json,features_json,realized_r,
                       max_profit_pct,max_drawdown_pct FROM signals
                WHERE closed_at IS NOT NULL AND id<>COALESCE(?, -1)
                ORDER BY id DESC LIMIT 2000
            """, (dna.extras.get("signal_id") if isinstance(dna.extras, dict) else None,)).fetchall()
        found=[]
        for row in rows:
            candidate = TradeDNABuilder.from_signal(dict(row))
            similarity, matching, differences = self.compare(dna, candidate)
            if similarity < minimum_similarity:
                continue
            found.append(SimilarTrade(int(row["id"]), str(row["symbol"]), str(row["timeframe"]), str(row["side"]),
                                      str(row["status"]), similarity, float(row["realized_r"] or 0),
                                      float(row["max_profit_pct"] or 0), float(row["max_drawdown_pct"] or 0), matching, differences))
        return sorted(found, key=lambda x: x.similarity, reverse=True)[:limit]

    @staticmethod
    def summarize(cases: list[SimilarTrade]) -> dict[str, Any]:
        if not cases:
            return {"samples":0,"expected_r":0.0,"average_result_r":0.0,"reliability":"Insufficient",
                    "best_trade":None,"worst_trade":None,"cases":[]}
        weights=[max(.05,c.similarity/100) for c in cases]
        total=sum(weights)
        expected=sum(c.realized_r*w for c,w in zip(cases,weights))/total
        best=max(cases,key=lambda c:c.realized_r); worst=min(cases,key=lambda c:c.realized_r)
        n=len(cases)
        reliability="High" if n>=50 else "Moderate" if n>=20 else "Low" if n>=8 else "Insufficient"
        return {"samples":n,"expected_r":round(expected,2),
                "average_result_r":round(sum(c.realized_r for c in cases)/n,2),
                "average_similarity":round(sum(c.similarity for c in cases)/n,1),
                "win_rate":round(sum(c.realized_r>0 for c in cases)/n*100,1),"reliability":reliability,
                "best_trade":asdict(best),"worst_trade":asdict(worst),"cases":[asdict(c) for c in cases]}
