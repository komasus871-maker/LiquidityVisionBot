from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def _number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _text(value: Any, default: str = "UNKNOWN") -> str:
    if isinstance(value, dict):
        value = value.get("code") or value.get("label") or value.get("zone") or value.get("status")
    text = str(value or "").replace("✅", "").replace("⚠️", "").strip()
    return text or default


@dataclass(slots=True)
class TradeDNA:
    version: int = 2
    fingerprint: str = ""
    created_at: str = ""
    symbol: str = ""
    timeframe: str = ""
    side: str = "NEUTRAL"
    trend: str = "UNKNOWN"
    structure: str = "UNKNOWN"
    bos: str = "UNKNOWN"
    choch: str = "UNKNOWN"
    liquidity: str = "UNKNOWN"
    liquidity_event: str = "UNKNOWN"
    sweep: str = "UNKNOWN"
    order_block: str = "UNKNOWN"
    breaker: str = "UNKNOWN"
    mitigation: str = "UNKNOWN"
    fvg: str = "UNKNOWN"
    premium_discount: str = "UNKNOWN"
    session: str = "UNKNOWN"
    market_regime: str = "UNKNOWN"
    htf_alignment: str = "UNKNOWN"
    macd: str = "UNKNOWN"
    volume: str = "UNKNOWN"
    displacement: str = "UNKNOWN"
    ema50: float = 0.0
    ema200: float = 0.0
    ema50_distance_pct: float = 0.0
    ema200_distance_pct: float = 0.0
    rsi: float = 50.0
    atr: float = 0.0
    atr_pct: float = 0.0
    entry: float = 0.0
    stop: float = 0.0
    tp1: float = 0.0
    tp2: float = 0.0
    tp3: float = 0.0
    risk_pct: float = 0.0
    rr: float = 0.0
    confidence: float = 0.0
    direction_score: float = 0.0
    entry_quality: float = 0.0
    risk_quality: float = 0.0
    readiness: float = 0.0
    trade_health: float = 0.0
    ai_grade: str = "N/A"
    execution_status: str = "UNKNOWN"
    opportunity_category: str = "UNKNOWN"
    decision_action: str = "OBSERVE"
    score_components: dict[str, float] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class TradeDNABuilder:
    """Builds the immutable, canonical feature snapshot used by all learning modules."""

    DNA_KEYS = {
        "trend", "structure", "bos", "choch", "liquidity", "liquidity_event", "sweep",
        "order_block", "breaker", "mitigation", "fvg", "premium", "session", "market_regime",
        "regime", "htf_alignment", "multi_timeframe", "macd", "volume", "displacement",
        "ema50", "ema200", "rsi", "atr", "entry", "stop", "tp1", "tp2", "tp3", "rr",
        "confidence", "direction_score", "entry_quality", "risk_quality", "execution_readiness",
        "readiness", "trade_health", "health_score", "ai_grade", "execution_status",
        "opportunity_category", "decision_action", "score_components", "direction_breakdown",
        "global_context", "execution_bias", "trade_quality_stars", "expected_path", "triggers",
    }

    @classmethod
    def build(cls, analysis: dict[str, Any], *, symbol: str = "", timeframe: str = "") -> TradeDNA:
        price = _number(analysis.get("price") or analysis.get("entry"))
        entry = _number(analysis.get("entry") or price)
        stop = _number(analysis.get("stop"))
        ema50 = _number(analysis.get("ema50"))
        ema200 = _number(analysis.get("ema200"))
        atr = _number(analysis.get("atr"))
        premium = analysis.get("premium") or {}
        premium_zone = premium.get("zone") if isinstance(premium, dict) else premium
        regime = analysis.get("market_regime") or analysis.get("regime") or {}
        htf = analysis.get("htf_alignment") or analysis.get("multi_timeframe") or analysis.get("global_context")
        risk_pct = abs(entry - stop) / entry * 100 if entry else 0.0
        components = analysis.get("score_components") or analysis.get("direction_breakdown") or {}
        normalized_components = {
            str(k): round(_number(v), 4) for k, v in components.items()
        } if isinstance(components, dict) else {}
        tags = []
        for key in ("trend", "structure", "bos", "choch", "sweep", "order_block", "fvg", "market_regime"):
            value = _text(analysis.get(key) if key != "market_regime" else regime, "")
            if value:
                tags.append(f"{key}:{value.lower()}")
        extras = {key: analysis.get(key) for key in cls.DNA_KEYS if key in analysis and key not in {
            "score_components", "direction_breakdown"
        }}
        canonical = {
            "symbol": symbol.upper(), "timeframe": timeframe.lower(),
            "side": _text(analysis.get("direction") or analysis.get("side"), "NEUTRAL").upper(),
            "trend": _text(analysis.get("trend")), "structure": _text(analysis.get("structure")),
            "bos": _text(analysis.get("bos")), "choch": _text(analysis.get("choch")),
            "liquidity": _text(analysis.get("liquidity")),
            "liquidity_event": _text(analysis.get("liquidity_event") or analysis.get("liquidity")),
            "sweep": _text(analysis.get("sweep")), "order_block": _text(analysis.get("order_block")),
            "breaker": _text(analysis.get("breaker")), "mitigation": _text(analysis.get("mitigation")),
            "fvg": _text(analysis.get("fvg")), "premium_discount": _text(premium_zone),
            "session": _text(analysis.get("session")), "market_regime": _text(regime),
            "htf_alignment": _text(htf), "macd": _text(analysis.get("macd")),
            "volume": _text(analysis.get("volume")), "displacement": _text(analysis.get("displacement")),
            "ema50": ema50, "ema200": ema200,
            "ema50_distance_pct": ((price - ema50) / price * 100) if price and ema50 else 0.0,
            "ema200_distance_pct": ((price - ema200) / price * 100) if price and ema200 else 0.0,
            "rsi": _number(analysis.get("rsi"), 50.0), "atr": atr,
            "atr_pct": atr / price * 100 if price and atr else 0.0,
            "entry": entry, "stop": stop, "tp1": _number(analysis.get("tp1")),
            "tp2": _number(analysis.get("tp2")), "tp3": _number(analysis.get("tp3")),
            "risk_pct": risk_pct, "rr": _number(analysis.get("rr")),
            "confidence": _number(analysis.get("confidence")),
            "direction_score": _number(analysis.get("direction_score")),
            "entry_quality": _number(analysis.get("entry_quality")),
            "risk_quality": _number(analysis.get("risk_quality")),
            "readiness": _number(analysis.get("execution_readiness") or analysis.get("readiness")),
            "trade_health": _number(analysis.get("health_score") or analysis.get("trade_health")),
            "ai_grade": _text(analysis.get("ai_grade"), "N/A"),
            "execution_status": _text(analysis.get("execution_status")),
            "opportunity_category": _text(analysis.get("opportunity_category")),
            "decision_action": _text(analysis.get("decision_action"), "OBSERVE"),
            "score_components": normalized_components, "tags": sorted(set(tags)), "extras": extras,
        }
        fingerprint_payload = {k: v for k, v in canonical.items() if k not in {"extras"}}
        fingerprint = hashlib.sha256(json.dumps(fingerprint_payload, sort_keys=True, default=str).encode()).hexdigest()[:24]
        return TradeDNA(
            fingerprint=fingerprint, created_at=datetime.now(timezone.utc).isoformat(), **canonical
        )

    @classmethod
    def from_signal(cls, signal: dict[str, Any]) -> TradeDNA:
        raw = signal.get("trade_dna_json") or signal.get("features_json") or "{}"
        try:
            payload = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        if "fingerprint" in payload and "version" in payload:
            valid = {f.name for f in TradeDNA.__dataclass_fields__.values()}
            return TradeDNA(**{k: v for k, v in payload.items() if k in valid})
        payload.update({k: signal.get(k) for k in ("entry", "stop", "tp1", "tp2", "tp3", "rr", "confidence")})
        payload["direction"] = signal.get("side")
        return cls.build(payload, symbol=str(signal.get("symbol") or ""), timeframe=str(signal.get("timeframe") or ""))
