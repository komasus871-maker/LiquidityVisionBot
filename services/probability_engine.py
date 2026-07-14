from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any

from database.database import connect


CLOSED_STATUSES = ("TP3", "STOP", "BREAKEVEN", "INVALIDATED", "EXPIRED")


@dataclass(slots=True)
class SimilarCase:
    signal_id: int
    symbol: str
    timeframe: str
    side: str
    status: str
    similarity: float
    tp1_hit: bool
    tp2_hit: bool
    tp3_hit: bool
    stop_hit: bool
    mfe: float
    mae: float
    duration_minutes: float = 0.0
    realized_r: float = 0.0


class ProbabilityEngine:
    """Historical statistics and transparent similarity matching.

    This engine never invents probabilities. Percentages are shown only from
    completed signals stored in SQLite. Exact-setup statistics are preferred;
    feature similarity is used as a secondary discovery layer.
    """

    MIN_RELIABLE_SAMPLE = 30
    MIN_DISPLAY_SAMPLE = 5

    @staticmethod
    def _normalize(value: Any) -> str:
        text = str(value)
        text = re.sub(r"\([^)]*\)", "", text)
        text = re.sub(r"[-+]?\d+(?:\.\d+)?", "", text)
        text = re.sub(r"[^A-Za-zА-Яа-я/ ]+", " ", text)
        return re.sub(r"\s+", " ", text).strip().lower()

    @classmethod
    def _tokens(cls, features: dict[str, Any]) -> set[str]:
        tokens: set[str] = set()
        meaningful_keys = {
            "trend", "structure", "bos", "choch", "liquidity", "sweep", "order_block",
            "breaker", "mitigation", "fvg", "volume", "displacement", "macd",
            "market_bias", "execution_status", "opportunity_category",
        }
        for key, value in features.items():
            if key not in meaningful_keys and key != "premium":
                continue
            if value in (None, "", [], {}):
                continue
            if key == "premium" and isinstance(value, dict):
                zone = value.get("zone")
                if zone:
                    tokens.add(f"premium={cls._normalize(zone)}")
                continue
            normalized = cls._normalize(value)
            if normalized:
                tokens.add(f"{key}={normalized}")
        return tokens

    @classmethod
    def _similarity(cls, left: dict[str, Any], right: dict[str, Any]) -> float:
        a = cls._tokens(left)
        b = cls._tokens(right)
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b) * 100.0

    def exact_stats(self, setup_key: str, timeframe: str, side: str) -> dict[str, Any]:
        with connect() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS samples,
                    SUM(CASE WHEN tp1_hit_at IS NOT NULL THEN 1 ELSE 0 END) AS tp1_hits,
                    SUM(CASE WHEN tp2_hit_at IS NOT NULL THEN 1 ELSE 0 END) AS tp2_hits,
                    SUM(CASE WHEN tp3_hit_at IS NOT NULL THEN 1 ELSE 0 END) AS tp3_hits,
                    SUM(CASE WHEN stop_hit_at IS NOT NULL THEN 1 ELSE 0 END) AS stop_hits,
                    AVG(max_profit_pct) AS avg_mfe,
                    AVG(max_drawdown_pct) AS avg_mae
                FROM signals
                WHERE setup_key=? AND timeframe=? AND side=?
                  AND status IN ('TP3','STOP','BREAKEVEN','INVALIDATED','EXPIRED')
                """,
                (setup_key, timeframe, side),
            ).fetchone()
        samples = int(row["samples"] or 0)
        result = {
            "samples": samples,
            "tp1_rate": 0.0,
            "tp2_rate": 0.0,
            "tp3_rate": 0.0,
            "stop_rate": 0.0,
            "avg_mfe": round(float(row["avg_mfe"] or 0), 2),
            "avg_mae": round(float(row["avg_mae"] or 0), 2),
            "reliability": self._reliability(samples),
            "sufficient": samples >= self.MIN_RELIABLE_SAMPLE,
        }
        if samples:
            for field in ("tp1", "tp2", "tp3", "stop"):
                result[f"{field}_rate"] = round(float(row[f"{field}_hits"] or 0) / samples * 100, 1)
        return result

    @staticmethod
    def _reliability(samples: int) -> str:
        if samples >= 200:
            return "Very High"
        if samples >= 100:
            return "High"
        if samples >= 30:
            return "Moderate"
        if samples >= 10:
            return "Low"
        return "Insufficient"

    def similar_cases(
        self,
        *,
        features: dict[str, Any],
        side: str,
        timeframe: str,
        symbol: str | None = None,
        limit: int = 8,
        minimum_similarity: float = 30.0,
    ) -> list[SimilarCase]:
        with connect() as conn:
            rows = conn.execute(
                """
                SELECT id,symbol,timeframe,side,status,features_json,
                       tp1_hit_at,tp2_hit_at,tp3_hit_at,stop_hit_at,
                       max_profit_pct,max_drawdown_pct
                FROM signals
                WHERE side=? AND timeframe=?
                  AND status IN ('TP3','STOP','BREAKEVEN','INVALIDATED','EXPIRED')
                ORDER BY id DESC LIMIT 1000
                """,
                (side, timeframe),
            ).fetchall()

        found: list[SimilarCase] = []
        for row in rows:
            try:
                candidate = json.loads(row["features_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                candidate = {}
            similarity = self._similarity(features, candidate)
            if symbol and row["symbol"] == symbol:
                similarity = min(100.0, similarity + 5.0)
            if similarity < minimum_similarity:
                continue
            found.append(
                SimilarCase(
                    signal_id=int(row["id"]),
                    symbol=str(row["symbol"]),
                    timeframe=str(row["timeframe"]),
                    side=str(row["side"]),
                    status=str(row["status"]),
                    similarity=round(similarity, 1),
                    tp1_hit=bool(row["tp1_hit_at"]),
                    tp2_hit=bool(row["tp2_hit_at"]),
                    tp3_hit=bool(row["tp3_hit_at"]),
                    stop_hit=bool(row["stop_hit_at"]),
                    mfe=round(float(row["max_profit_pct"] or 0), 2),
                    mae=round(float(row["max_drawdown_pct"] or 0), 2),
                )
            )
        found.sort(key=lambda item: item.similarity, reverse=True)
        return found[:limit]

    def similar_stats(self, cases: list[SimilarCase]) -> dict[str, Any]:
        samples = len(cases)
        result = {
            "samples": samples,
            "tp1_rate": 0.0,
            "tp2_rate": 0.0,
            "tp3_rate": 0.0,
            "stop_rate": 0.0,
            "avg_similarity": 0.0,
            "avg_mfe": 0.0,
            "avg_mae": 0.0,
            "reliability": self._reliability(samples),
            "sufficient": samples >= self.MIN_RELIABLE_SAMPLE,
        }
        if not samples:
            return result
        result.update({
            "tp1_rate": round(sum(x.tp1_hit for x in cases) / samples * 100, 1),
            "tp2_rate": round(sum(x.tp2_hit for x in cases) / samples * 100, 1),
            "tp3_rate": round(sum(x.tp3_hit for x in cases) / samples * 100, 1),
            "stop_rate": round(sum(x.stop_hit for x in cases) / samples * 100, 1),
            "avg_similarity": round(sum(x.similarity for x in cases) / samples, 1),
            "avg_mfe": round(sum(x.mfe for x in cases) / samples, 2),
            "avg_mae": round(sum(x.mae for x in cases) / samples, 2),
        })
        return result

    @staticmethod
    def _wilson(success_weight: float, total_weight: float, z: float = 1.96) -> tuple[float, float]:
        if total_weight <= 0:
            return 0.0, 0.0
        p = success_weight / total_weight
        denom = 1 + z * z / total_weight
        center = (p + z * z / (2 * total_weight)) / denom
        margin = z * ((p * (1 - p) / total_weight + z * z / (4 * total_weight * total_weight)) ** 0.5) / denom
        return max(0.0, center - margin) * 100, min(1.0, center + margin) * 100

    def weighted_stats(self, cases: list[SimilarCase]) -> dict[str, Any]:
        samples = len(cases)
        if not samples:
            return {"samples": 0, "effective_samples": 0.0, "estimated": False}
        weights = [max(0.01, min(1.0, float(c.similarity) / 100.0)) for c in cases]
        total = sum(weights)
        effective = total * total / max(sum(w * w for w in weights), 1e-12)
        result: dict[str, Any] = {
            "samples": samples,
            "effective_samples": round(effective, 2),
            "avg_similarity": round(sum(c.similarity * w for c, w in zip(cases, weights)) / total, 1),
            "avg_mfe": round(sum(c.mfe * w for c, w in zip(cases, weights)) / total, 2),
            "avg_mae": round(sum(c.mae * w for c, w in zip(cases, weights)) / total, 2),
            "avg_duration_minutes": round(sum(c.duration_minutes * w for c, w in zip(cases, weights)) / total, 1),
            "avg_realized_r": round(sum(c.realized_r * w for c, w in zip(cases, weights)) / total, 2),
            "estimated": effective >= 3.0,
            "reliability": self._reliability(int(effective)),
        }
        for name, attr in (("tp1", "tp1_hit"), ("tp2", "tp2_hit"), ("tp3", "tp3_hit"), ("stop", "stop_hit")):
            success = sum(w for c, w in zip(cases, weights) if getattr(c, attr))
            rate = success / total * 100
            low, high = self._wilson(success, effective)
            result[f"{name}_rate"] = round(rate, 1)
            result[f"{name}_low"] = round(low, 1)
            result[f"{name}_high"] = round(high, 1)
        return result

    def enrich(self, analysis: dict[str, Any], *, symbol: str, timeframe: str, setup_key: str) -> dict[str, Any]:
        features = {key: analysis.get(key) for key in (
            "trend", "structure", "bos", "choch", "liquidity", "sweep", "order_block",
            "breaker", "mitigation", "fvg", "premium", "volume", "displacement", "macd",
            "market_bias", "execution_status", "opportunity_category",
        )}
        exact = self.exact_stats(setup_key, timeframe, analysis.get("direction", "LONG"))
        cases = self.similar_cases(
            features=features,
            side=analysis.get("direction", "LONG"),
            timeframe=timeframe,
            symbol=symbol,
        )
        analysis["historical_probability"] = exact
        analysis["similar_cases"] = [asdict(case) for case in cases]
        analysis["similar_stats"] = self.similar_stats(cases)
        return analysis

    def live_context(self, signal: dict[str, Any]) -> dict[str, Any]:
        """Return historical probability context for a persisted live signal.

        Exact setup statistics are preferred. Similar cases are used only when
        exact history is too small. No synthetic percentage is generated.
        """
        setup_key = str(signal.get("setup_key") or "")
        timeframe = str(signal.get("timeframe") or "1h")
        side = str(signal.get("side") or "LONG")
        exact = self.exact_stats(setup_key, timeframe, side)
        try:
            features = json.loads(signal.get("features_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            features = {}
        cases = self.similar_cases(
            features=features,
            side=side,
            timeframe=timeframe,
            symbol=str(signal.get("symbol") or "") or None,
            limit=50,
            minimum_similarity=25.0,
        )
        similar = self.similar_stats(cases)
        source = "exact" if exact["samples"] >= self.MIN_DISPLAY_SAMPLE else "similar"
        chosen = exact if source == "exact" else similar
        samples = int(chosen.get("samples") or 0)
        sufficient = samples >= self.MIN_RELIABLE_SAMPLE
        return {
            "source": source,
            "samples": samples,
            "tp1_rate": float(chosen.get("tp1_rate") or 0) if sufficient else 0.0,
            "tp2_rate": float(chosen.get("tp2_rate") or 0) if sufficient else 0.0,
            "tp3_rate": float(chosen.get("tp3_rate") or 0) if sufficient else 0.0,
            "stop_rate": float(chosen.get("stop_rate") or 0) if sufficient else 0.0,
            "reliability": str(chosen.get("reliability") or "Insufficient"),
            "sufficient": sufficient,
            "disabled_reason": None if sufficient else f"Need at least {self.MIN_RELIABLE_SAMPLE} completed comparable trades; have {samples}.",
            "avg_mfe": float(chosen.get("avg_mfe") or 0),
            "avg_mae": float(chosen.get("avg_mae") or 0),
        }
