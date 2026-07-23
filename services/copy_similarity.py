from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Iterable

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


class CopySimilarityService:
    """Similarity search over closed executions and resolved zero-exposure rejections."""

    MIN_SCORE = 0.45

    def __init__(self) -> None:
        self.builder = StrategyGenomeBuilder()

    def snapshot(self, signal: dict[str, Any]) -> tuple[str, str]:
        genome = self.builder.build(signal)
        return json.dumps(genome, sort_keys=True, ensure_ascii=False), self.builder.fingerprint(genome)

    def report_for_signal(self, telegram_id: int, signal_id: int, *, limit: int = 8) -> dict[str, Any]:
        with connect() as conn:
            row = conn.execute("SELECT * FROM signals WHERE id=?", (signal_id,)).fetchone()
        if not row:
            raise LookupError(f"Signal #{signal_id} was not found")
        return self.report(telegram_id, dict(row), limit=limit)

    def latest_report(self, telegram_id: int, *, limit: int = 8) -> dict[str, Any]:
        with connect() as conn:
            row = conn.execute(
                """SELECT s.* FROM paper_positions p JOIN signals s ON s.id=p.signal_id
                   WHERE p.telegram_id=? ORDER BY p.id DESC LIMIT 1""",
                (telegram_id,),
            ).fetchone()
        if not row:
            raise LookupError("No copy execution attempt exists yet")
        return self.report(telegram_id, dict(row), limit=limit)

    def report(self, telegram_id: int, signal: dict[str, Any], *, limit: int = 8) -> dict[str, Any]:
        target = self.builder.build(signal)
        target_id = int(signal.get("id") or 0)
        candidates = self._history(telegram_id, exclude_signal_id=target_id)
        matches: list[SimilarTrade] = []
        for row in candidates:
            genome = _json_dict(row.get("genome_json")) or self.builder.build(row)
            score = self.similarity(target, genome)
            if score < self.MIN_SCORE:
                continue
            shadow = row.get("status") == "REJECTED"
            matches.append(SimilarTrade(
                signal_id=int(row["signal_id"]), symbol=str(row["symbol"]),
                timeframe=str(row["timeframe"]), side=str(row["side"]),
                result=str(row.get("shadow_result") if shadow else row.get("close_reason") or row.get("result") or "CLOSED"),
                realized_r=float(row.get("shadow_realized_r") if shadow else row.get("realized_r") or 0.0),
                mfe=float(row.get("max_profit_pct") or 0.0), mae=float(row.get("max_drawdown_pct") or 0.0),
                similarity=score * 100.0, source="SHADOW" if shadow else "EXECUTED",
            ))
        matches.sort(key=lambda item: (item.similarity, abs(item.realized_r)), reverse=True)
        selected = matches[: max(1, min(int(limit), 20))]
        sample = len(selected)
        realized = [item.realized_r for item in selected]
        return {
            "signal_id": target_id,
            "fingerprint": self.builder.fingerprint(target),
            "found": sample,
            "win_rate": sum(value > 0 for value in realized) / sample * 100.0 if sample else 0.0,
            "average_r": sum(realized) / sample if sample else 0.0,
            "average_mfe": sum(item.mfe for item in selected) / sample if sample else 0.0,
            "average_mae": sum(item.mae for item in selected) / sample if sample else 0.0,
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

    @staticmethod
    def similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
        earned = possible = 0.0
        weights = {"side": 3.0, "timeframe": 2.0, "setup_key": 2.5, "market_regime": 2.0}
        for key in _CATEGORICAL_KEYS:
            if key not in left or key not in right:
                continue
            weight = weights.get(key, 1.0)
            possible += weight
            earned += weight if str(left[key]).lower() == str(right[key]).lower() else 0.0
        for key in _BOOLEAN_KEYS:
            if key not in left or key not in right:
                continue
            possible += 0.75
            earned += 0.75 if bool(left[key]) == bool(right[key]) else 0.0
        scales = {"confidence": 30.0, "rsi": 35.0, "fear": 50.0, "rr": 3.0,
                  "bull_score": 50.0, "bear_score": 50.0, "atr_pct": 5.0,
                  "volume_ratio": 3.0, "funding": 0.01, "entry_quality": 50.0,
                  "risk_quality": 50.0, "readiness": 50.0}
        for key in _NUMERIC_KEYS:
            if key not in left or key not in right:
                continue
            possible += 1.0
            distance = abs(float(left[key]) - float(right[key])) / scales.get(key, 1.0)
            earned += max(0.0, 1.0 - distance)
        return earned / possible if possible else 0.0
