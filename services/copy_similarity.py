from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any

from database.database import connect


_CATEGORICAL_KEYS = (
    "side", "timeframe", "setup_key", "market_regime", "market_bias", "session",
    "structure", "trend", "execution_status",
)
_BOOLEAN_KEYS = (
    "bos", "choch", "liquidity", "sweep", "order_block", "breaker", "mitigation",
    "fvg", "premium", "volume", "displacement",
)
_NUMERIC_KEYS = (
    "confidence", "bull_score", "bear_score", "rsi", "atr_pct", "volume_ratio",
    "funding", "fear", "rr", "entry_quality", "risk_quality", "readiness",
)

_FEATURE_GROUPS: dict[str, tuple[str, ...]] = {
    "Structure": ("side", "structure", "trend", "market_bias", "bos", "choch"),
    "Liquidity": ("liquidity", "sweep", "order_block", "breaker", "mitigation", "fvg", "premium"),
    "Market": ("timeframe", "market_regime", "session", "atr_pct", "funding", "fear"),
    "Indicators": ("rsi", "volume", "volume_ratio", "displacement", "bull_score", "bear_score"),
    "Execution": ("setup_key", "execution_status", "confidence", "rr", "entry_quality", "risk_quality", "readiness"),
}

_CATEGORICAL_WEIGHTS = {
    "side": 3.0,
    "timeframe": 2.0,
    "setup_key": 2.5,
    "market_regime": 2.0,
}
_NUMERIC_SCALES = {
    "confidence": 30.0,
    "rsi": 35.0,
    "fear": 50.0,
    "rr": 3.0,
    "bull_score": 50.0,
    "bear_score": 50.0,
    "atr_pct": 5.0,
    "volume_ratio": 3.0,
    "funding": 0.01,
    "entry_quality": 50.0,
    "risk_quality": 50.0,
    "readiness": 50.0,
}


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _flatten(value: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in value.items():
        name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(item, dict):
            result.update(_flatten(item, name))
        else:
            result[name] = item
            result.setdefault(str(key), item)
    return result


def _first(source: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in source and source[key] is not None:
            return source[key]
    return None


def _normalise_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "yes", "1", "bullish", "present", "confirmed"}:
        return True
    if text in {"false", "no", "0", "bearish", "absent", "none"}:
        return False
    return None


def _normalise_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _feature_label(key: str) -> str:
    labels = {
        "side": "Direction",
        "timeframe": "Timeframe",
        "setup_key": "Setup",
        "market_regime": "Market regime",
        "market_bias": "Market bias",
        "execution_status": "Execution status",
        "order_block": "Order block",
        "atr_pct": "ATR profile",
        "volume_ratio": "Relative volume",
        "entry_quality": "Entry quality",
        "risk_quality": "Risk quality",
    }
    return labels.get(key, key.replace("_", " ").title())


class StrategyGenomeBuilder:
    """Builds a deterministic, execution-time feature snapshot from a signal."""

    VERSION = 1

    def build(self, signal: dict[str, Any]) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        for field in ("features_json", "trade_dna_json", "intelligence_json"):
            merged.update(_flatten(_json_dict(signal.get(field))))
        merged.update({key: value for key, value in signal.items() if value is not None})

        genome: dict[str, Any] = {
            "version": self.VERSION,
            "symbol": str(signal.get("symbol") or "").upper(),
            "side": str(signal.get("side") or "").upper(),
            "timeframe": str(signal.get("timeframe") or "").lower(),
            "setup_key": str(signal.get("setup_key") or "unknown"),
        }
        aliases = {
            "market_regime": ("market_regime", "regime", "regime.name"),
            "market_bias": ("market_bias", "bias"),
            "session": ("session", "market_session"),
            "structure": ("structure", "market_structure"),
            "trend": ("trend", "trend_direction"),
            "execution_status": ("execution_status", "recommendation"),
            "atr_pct": ("atr_pct", "atr_percent", "atr_percentage"),
            "volume_ratio": ("volume_ratio", "relative_volume", "rv"),
            "funding": ("funding", "funding_rate"),
            "fear": ("fear", "fear_greed", "fear_greed_index"),
            "entry_quality": ("entry_quality",),
            "risk_quality": ("risk_quality",),
            "readiness": ("readiness",),
        }
        for key in _CATEGORICAL_KEYS:
            if key in genome:
                continue
            value = _first(merged, *aliases.get(key, (key,)))
            if value is not None:
                genome[key] = str(value).lower()
        for key in _BOOLEAN_KEYS:
            value = _normalise_bool(_first(merged, key))
            if value is not None:
                genome[key] = value
        for key in _NUMERIC_KEYS:
            value = _normalise_number(_first(merged, *aliases.get(key, (key,))))
            if value is not None:
                genome[key] = round(value, 8)
        return genome

    @staticmethod
    def fingerprint(genome: dict[str, Any]) -> str:
        payload = json.dumps(genome, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class FeatureScore:
    key: str
    label: str
    group: str
    score: float
    weight: float
    target: Any
    candidate: Any


@dataclass(frozen=True)
class SimilarTrade:
    signal_id: int
    symbol: str
    timeframe: str
    side: str
    result: str
    realized_r: float
    mfe: float
    mae: float
    similarity: float
    source: str
    matched_features: tuple[str, ...]
    different_features: tuple[str, ...]
    group_scores: dict[str, float]


class CopySimilarityService:
    """Explainable similarity over closed executions and resolved shadow outcomes."""

    MIN_SCORE = 0.45

    def __init__(self) -> None:
        self.builder = StrategyGenomeBuilder()

    def snapshot(self, signal: dict[str, Any]) -> tuple[str, str]:
        genome = self.builder.build(signal)
        return json.dumps(genome, sort_keys=True, ensure_ascii=False), self.builder.fingerprint(genome)

    def report_for_signal(self, telegram_id: int, signal_id: int, *, limit: int = 8) -> dict[str, Any]:
        return self.report(telegram_id, self._signal(signal_id), limit=limit)

    def latest_report(self, telegram_id: int, *, limit: int = 8) -> dict[str, Any]:
        return self.report(telegram_id, self._latest_signal(telegram_id), limit=limit)

    def genome_for_signal(self, signal_id: int) -> dict[str, Any]:
        signal = self._signal(signal_id)
        genome = self.builder.build(signal)
        return {
            "signal_id": int(signal.get("id") or 0),
            "fingerprint": self.builder.fingerprint(genome),
            "genome": genome,
            "groups": self.grouped_genome(genome),
        }

    def latest_genome(self, telegram_id: int) -> dict[str, Any]:
        signal = self._latest_signal(telegram_id)
        genome = self.builder.build(signal)
        return {
            "signal_id": int(signal.get("id") or 0),
            "fingerprint": self.builder.fingerprint(genome),
            "genome": genome,
            "groups": self.grouped_genome(genome),
        }

    def _signal(self, signal_id: int) -> dict[str, Any]:
        with connect() as conn:
            row = conn.execute("SELECT * FROM signals WHERE id=?", (signal_id,)).fetchone()
        if not row:
            raise LookupError(f"Signal #{signal_id} was not found")
        return dict(row)

    def _latest_signal(self, telegram_id: int) -> dict[str, Any]:
        with connect() as conn:
            row = conn.execute(
                """SELECT s.* FROM paper_positions p JOIN signals s ON s.id=p.signal_id
                   WHERE p.telegram_id=? ORDER BY p.id DESC LIMIT 1""",
                (telegram_id,),
            ).fetchone()
        if not row:
            raise LookupError("No copy execution attempt exists yet")
        return dict(row)

    def report(self, telegram_id: int, signal: dict[str, Any], *, limit: int = 8) -> dict[str, Any]:
        target = self.builder.build(signal)
        target_id = int(signal.get("id") or 0)
        matches: list[SimilarTrade] = []
        for row in self._history(telegram_id, exclude_signal_id=target_id):
            genome = _json_dict(row.get("genome_json")) or self.builder.build(row)
            detail = self.similarity_details(target, genome)
            if detail["overall"] < self.MIN_SCORE:
                continue
            shadow = row.get("status") == "REJECTED"
            matches.append(SimilarTrade(
                signal_id=int(row["signal_id"]),
                symbol=str(row["symbol"]),
                timeframe=str(row["timeframe"]),
                side=str(row["side"]),
                result=str(row.get("shadow_result") if shadow else row.get("close_reason") or row.get("result") or "CLOSED"),
                realized_r=float(row.get("shadow_realized_r") if shadow else row.get("realized_r") or 0.0),
                mfe=float(row.get("max_profit_pct") or 0.0),
                mae=float(row.get("max_drawdown_pct") or 0.0),
                similarity=detail["overall"] * 100.0,
                source="SHADOW" if shadow else "EXECUTED",
                matched_features=tuple(detail["matched_features"][:5]),
                different_features=tuple(detail["different_features"][:3]),
                group_scores={key: value * 100.0 for key, value in detail["group_scores"].items()},
            ))
        matches.sort(key=lambda item: (item.similarity, abs(item.realized_r)), reverse=True)
        selected = matches[: max(1, min(int(limit), 20))]
        sample = len(matches)
        realized = [item.realized_r for item in matches]
        average_similarity = sum(item.similarity for item in matches) / sample if sample else 0.0
        return {
            "signal_id": target_id,
            "fingerprint": self.builder.fingerprint(target),
            "found": sample,
            "shown": len(selected),
            "win_rate": sum(value > 0 for value in realized) / sample * 100.0 if sample else 0.0,
            "average_r": sum(realized) / sample if sample else 0.0,
            "average_mfe": sum(item.mfe for item in matches) / sample if sample else 0.0,
            "average_mae": sum(item.mae for item in matches) / sample if sample else 0.0,
            "average_similarity": average_similarity,
            "statistical_confidence": self.statistical_confidence(sample, average_similarity),
            "breakdown": self.aggregate_breakdown(matches),
            "top_matching_features": self.aggregate_features(matches, matched=True),
            "largest_differences": self.aggregate_features(matches, matched=False),
            "matches": selected,
        }

    def _history(self, telegram_id: int, *, exclude_signal_id: int) -> list[dict[str, Any]]:
        with connect() as conn:
            rows = conn.execute(
                """SELECT p.*,s.setup_key,s.features_json,s.trade_dna_json,s.intelligence_json,
                          s.confidence,s.dynamic_confidence,s.bull_score,s.bear_score,s.rr,
                          s.max_profit_pct,s.max_drawdown_pct,s.result
                   FROM paper_positions p JOIN signals s ON s.id=p.signal_id
                   WHERE p.telegram_id=? AND p.signal_id<>? AND
                         (p.status='CLOSED' OR (p.status='REJECTED' AND p.shadow_closed_at IS NOT NULL))
                   ORDER BY COALESCE(p.closed_at,p.shadow_closed_at) DESC,p.id DESC LIMIT 500""",
                (telegram_id, exclude_signal_id),
            ).fetchall()
        return [dict(row) for row in rows]

    @classmethod
    def similarity(cls, left: dict[str, Any], right: dict[str, Any]) -> float:
        return float(cls.similarity_details(left, right)["overall"])

    @classmethod
    def similarity_details(cls, left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
        features: list[FeatureScore] = []
        key_to_group = {key: group for group, keys in _FEATURE_GROUPS.items() for key in keys}

        for key in _CATEGORICAL_KEYS:
            if key not in left or key not in right:
                continue
            score = 1.0 if str(left[key]).lower() == str(right[key]).lower() else 0.0
            features.append(FeatureScore(key, _feature_label(key), key_to_group[key], score, _CATEGORICAL_WEIGHTS.get(key, 1.0), left[key], right[key]))
        for key in _BOOLEAN_KEYS:
            if key not in left or key not in right:
                continue
            score = 1.0 if bool(left[key]) == bool(right[key]) else 0.0
            features.append(FeatureScore(key, _feature_label(key), key_to_group[key], score, 0.75, left[key], right[key]))
        for key in _NUMERIC_KEYS:
            if key not in left or key not in right:
                continue
            distance = abs(float(left[key]) - float(right[key])) / _NUMERIC_SCALES.get(key, 1.0)
            score = max(0.0, 1.0 - distance)
            features.append(FeatureScore(key, _feature_label(key), key_to_group[key], score, 1.0, left[key], right[key]))

        possible = sum(item.weight for item in features)
        earned = sum(item.weight * item.score for item in features)
        group_scores: dict[str, float] = {}
        for group in _FEATURE_GROUPS:
            group_items = [item for item in features if item.group == group]
            group_weight = sum(item.weight for item in group_items)
            if group_weight:
                group_scores[group] = sum(item.weight * item.score for item in group_items) / group_weight

        ranked_matches = sorted((item for item in features if item.score >= 0.8), key=lambda item: item.weight * item.score, reverse=True)
        ranked_differences = sorted((item for item in features if item.score < 0.8), key=lambda item: item.weight * (1.0 - item.score), reverse=True)
        return {
            "overall": earned / possible if possible else 0.0,
            "group_scores": group_scores,
            "matched_features": [item.label for item in ranked_matches],
            "different_features": [item.label for item in ranked_differences],
            "feature_scores": features,
        }

    @staticmethod
    def aggregate_breakdown(matches: list[SimilarTrade]) -> dict[str, float]:
        if not matches:
            return {}
        result: dict[str, float] = {}
        for group in _FEATURE_GROUPS:
            values = [item.group_scores[group] for item in matches if group in item.group_scores]
            if values:
                result[group] = sum(values) / len(values)
        return result

    @staticmethod
    def aggregate_features(matches: list[SimilarTrade], *, matched: bool, limit: int = 5) -> list[str]:
        counts: dict[str, int] = {}
        for item in matches:
            values = item.matched_features if matched else item.different_features
            for feature in values:
                counts[feature] = counts.get(feature, 0) + 1
        return [key for key, _ in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:limit]]

    @staticmethod
    def statistical_confidence(sample: int, average_similarity: float) -> dict[str, Any]:
        if sample >= 30 and average_similarity >= 65.0:
            level, score = "HIGH", min(100.0, 75.0 + min(sample, 100) * 0.2)
        elif sample >= 10 and average_similarity >= 55.0:
            level, score = "MEDIUM", min(74.0, 45.0 + sample * 1.2)
        elif sample >= 5:
            level, score = "LOW", min(44.0, 20.0 + sample * 2.0)
        else:
            level, score = "VERY LOW", min(19.0, sample * 4.0)
        return {"level": level, "score": score, "sample_size": sample}

    @staticmethod
    def grouped_genome(genome: dict[str, Any]) -> dict[str, dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        known = set()
        for group, keys in _FEATURE_GROUPS.items():
            values = {key: genome[key] for key in keys if key in genome}
            if values:
                grouped[group] = values
                known.update(values)
        identity = {key: genome[key] for key in ("symbol", "version") if key in genome}
        if identity:
            grouped = {"Identity": identity, **grouped}
        extra = {key: value for key, value in genome.items() if key not in known and key not in identity}
        if extra:
            grouped["Other"] = extra
        return grouped
