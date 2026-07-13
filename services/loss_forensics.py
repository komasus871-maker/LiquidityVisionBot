from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class LossDiagnosis:
    classification: str
    severity: str
    confidence: float
    evidence: tuple[str, ...]
    recommendations: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["evidence"] = list(self.evidence)
        data["recommendations"] = list(self.recommendations)
        return data


class LossForensicsEngine:
    """Classify losing trades using facts captured at signal time.

    The engine is intentionally deterministic and explainable. It does not call
    every stop an error: a well-formed setup can still be a VALID_LOSS.
    """

    def diagnose(self, trade: Mapping[str, Any]) -> LossDiagnosis:
        meta = dict(trade.get("metadata") or {})
        regime = str(trade.get("regime") or meta.get("regime") or "UNKNOWN")
        readiness = float(trade.get("readiness") or meta.get("execution_readiness") or 0.0)
        mfe = float(trade.get("mfe_r") or 0.0)
        mae = float(trade.get("mae_r") or 0.0)
        bars_held = int(trade.get("bars_held") or 0)
        edge = abs(float(meta.get("directional_edge") or 0.0))
        entry_quality = float(meta.get("entry_quality") or 0.0)
        risk_quality = float(meta.get("risk_quality") or 0.0)
        blockers = meta.get("biggest_blockers") or []
        blockers_text = " ".join(str(x).lower() for x in blockers)

        evidence: list[str] = []
        recommendations: list[str] = []
        scores: Counter[str] = Counter()

        if regime in {"RANGING", "COMPRESSION", "TRANSITION", "UNKNOWN"}:
            scores["CHOP_FALSE_BREAKOUT"] += 6
            evidence.append(f"Signal executed in non-trending regime: {regime}")
            recommendations.append("Block live trend entries outside a confirmed trending regime")
        if regime == "VOLATILE_EXPANSION":
            scores["LATE_ENTRY"] += 4
            evidence.append("Signal entered during volatile expansion")
            recommendations.append("Require volatility normalization or a pullback after expansion")
        if mfe < 0.25 and mae >= 0.8:
            scores["WRONG_DIRECTION"] += 3
            evidence.append("Trade moved almost directly to the stop with negligible MFE")
            recommendations.append("Raise directional and higher-timeframe alignment requirements")
        if mfe >= 0.8 and float(trade.get("net_r") or 0.0) < 0:
            scores["PROFIT_PROTECTION_FAILURE"] += 4
            evidence.append(f"Trade reached {mfe:.2f}R MFE but finished negative")
            recommendations.append("Test earlier partial profit or adaptive break-even rules")
        if bars_held <= 2 and mae >= 0.9:
            scores["STOP_TOO_TIGHT"] += 2
            evidence.append("Stop was reached within the first two bars")
            recommendations.append("Compare structural invalidation distance with ATR noise")
        if readiness < 75 or entry_quality < 65:
            scores["INSUFFICIENT_CONFIRMATION"] += 3
            evidence.append(f"Weak admission quality: readiness={readiness:.1f}, entry={entry_quality:.1f}")
            recommendations.append("Increase paper-mode admission threshold until validated")
        if risk_quality < 55 or "rr below" in blockers_text or "atr volatility" in blockers_text:
            scores["RISK_MODEL_FAILURE"] += 3
            evidence.append(f"Risk quality was weak ({risk_quality:.1f})")
            recommendations.append("Reject trades with weak structural stop geometry or unstable volatility")
        if edge < 12:
            scores["INSUFFICIENT_CONFIRMATION"] += 2
            evidence.append(f"Directional edge was only {edge:.1f}")
        if not scores:
            return LossDiagnosis(
                "VALID_LOSS", "NORMAL", 55.0,
                ("No deterministic execution defect was identified",),
                ("Keep the trade in the sample and evaluate expectancy over a larger series",),
            )

        classification, points = scores.most_common(1)[0]
        total = sum(scores.values())
        confidence = min(95.0, 50.0 + points / max(total, 1) * 35.0 + min(total, 8) * 1.5)
        severity = "CRITICAL" if points >= 5 else "HIGH" if points >= 4 else "MEDIUM"
        return LossDiagnosis(
            classification=classification,
            severity=severity,
            confidence=round(confidence, 1),
            evidence=tuple(dict.fromkeys(evidence)),
            recommendations=tuple(dict.fromkeys(recommendations)),
        )

    def summarize(self, trades: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
        diagnoses = []
        for trade in trades:
            if float(trade.get("net_r") or 0.0) <= 0 and trade.get("entry_index") is not None:
                diagnosis = self.diagnose(trade)
                diagnoses.append({"trade": dict(trade), "diagnosis": diagnosis.as_dict()})
        counts = Counter(item["diagnosis"]["classification"] for item in diagnoses)
        return {
            "losses_analyzed": len(diagnoses),
            "classification_counts": dict(counts),
            "dominant_failure": counts.most_common(1)[0][0] if counts else None,
            "diagnoses": diagnoses,
        }
