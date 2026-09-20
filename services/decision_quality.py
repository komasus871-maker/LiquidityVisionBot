from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from services.conviction_engine import ConvictionEngine
from services.unified_decision import UnifiedDecisionEngine
from services.market_memory import MarketMemory
from services.decision_brain import DecisionBrain


class DecisionQualityEngine:
    """Post-processes raw analysis into a stricter, explainable decision.

    It does not invent a new directional model. It makes the existing model
    harder to execute, removes duplicate evidence, explains the planned entry,
    and exposes the expected path the setup must follow before activation.
    """

    EXECUTABLE = {"🟢 READY", "🟡 WAIT FOR TRIGGER", "🎯 WAIT FOR PULLBACK", "🔄 REVERSAL WATCH"}
    AUTHORITY = "DecisionQualityEngine"
    DECISION_VERSION = "decision-authority-v1"
    APPROVED = "APPROVED"
    NO_TRADE = "NO_TRADE"
    INVALID_DATA_STATES = {
        "INVALID", "STALE", "INCOMPLETE", "INSUFFICIENT", "FAILED", "CORRUPT",
        "GAPPED", "DUPLICATE", "CONFLICTING", "OUT_OF_ORDER", "PROVIDER_ERROR",
    }

    def __init__(self):
        self.unified = UnifiedDecisionEngine()
        self.memory = MarketMemory()
        self.brain = DecisionBrain()

    @staticmethod
    def _dedupe_text(items: list[str] | None) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for item in items or []:
            text = str(item).strip()
            key = text.lower().replace("strong ", "").replace("moderate ", "")
            key = " ".join(key.split())
            if key in seen:
                continue
            seen.add(key)
            result.append(text)
        return result

    @staticmethod
    def _dedupe_components(items: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        # Keep only the strongest occurrence of the same labelled factor.
        chosen: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for component in items or []:
            label = str(component.get("label") or "Factor").strip()
            key = " ".join(label.lower().replace("strong ", "").replace("moderate ", "").split())
            value = float(component.get("value") or 0)
            if key not in chosen:
                chosen[key] = dict(component)
                order.append(key)
            elif abs(value) > abs(float(chosen[key].get("value") or 0)):
                chosen[key] = dict(component)
        return [chosen[key] for key in order]

    @staticmethod
    def _entry_reasons(data: dict[str, Any]) -> list[str]:
        direction = str(data.get("direction") or "NEUTRAL")
        premium = data.get("premium") or {}
        zone = str(premium.get("zone") or "")
        reasons: list[str] = []

        if direction == "LONG" and "Discount" in zone:
            reasons.append("Planned inside the discount half of the dealing range")
        elif direction == "SHORT" and "Premium" in zone:
            reasons.append("Planned inside the premium half of the dealing range")

        for key, label in (
            ("order_block", "Order-block reaction area"),
            ("breaker", "Breaker-block reaction area"),
            ("mitigation", "Mitigation zone"),
            ("fvg", "Fair-value-gap rebalancing area"),
        ):
            value = str(data.get(key) or "")
            if value and "No " not in value and "Unknown" not in value:
                aligned = (direction == "LONG" and "Bullish" in value) or (direction == "SHORT" and "Bearish" in value)
                if aligned:
                    reasons.append(label)

        liquidity = str(data.get("liquidity") or "")
        if direction == "LONG" and "Lows" in liquidity:
            reasons.append("Nearby sell-side liquidity can fuel a reversal")
        elif direction == "SHORT" and "Highs" in liquidity:
            reasons.append("Nearby buy-side liquidity can fuel a reversal")

        if not reasons:
            reasons.append("Zone derived from volatility-adjusted execution geometry")
        return reasons[:4]

    @staticmethod
    def _expected_path(data: dict[str, Any]) -> list[str]:
        status = str(data.get("execution_status") or "WATCHLIST")
        path = ["Observe current price action"]
        if status == "🎯 WAIT FOR PULLBACK":
            path.append("Price reaches the planned entry zone")
        elif status in {"🟡 WAIT FOR TRIGGER", "🔄 REVERSAL WATCH", "🔵 WATCHLIST"}:
            path.append("Required structure/trigger appears")
        else:
            path.append("Execution conditions remain valid")
        path.append("Aligned reaction and displacement confirm")
        path.append("Trade activates")
        path.extend(["TP1", "TP2", "TP3"])
        return path

    @staticmethod
    def _why_exists(data: dict[str, Any]) -> str:
        direction = str(data.get("direction") or "NEUTRAL")
        trend = str(data.get("trend") or "")
        structure = str(data.get("structure") or "")
        if direction == "LONG":
            return (
                "The idea exists because the broader directional model still favors buyers. "
                f"Trend is {trend.replace('🟢 ', '').replace('🔴 ', '').replace('🟡 ', '')}; "
                f"structure is {structure.replace('🟢 ', '').replace('🔴 ', '').replace('🟡 ', '')}. "
                "It disappears if bearish structure becomes dominant before activation."
            )
        if direction == "SHORT":
            return (
                "The idea exists because the broader directional model still favors sellers. "
                f"Trend is {trend.replace('🟢 ', '').replace('🔴 ', '').replace('🟡 ', '')}; "
                f"structure is {structure.replace('🟢 ', '').replace('🔴 ', '').replace('🟡 ', '')}. "
                "It disappears if bullish structure becomes dominant before activation."
            )
        return "The market is two-sided, so no directional trade thesis is currently trusted."

    @staticmethod
    def _data_quality(data: dict[str, Any]) -> tuple[str, bool]:
        quality = data.get("data_quality")
        if quality is None:
            quality = (data.get("market_intelligence") or {}).get("data_quality")
        if isinstance(quality, str):
            return quality.upper(), False
        if not isinstance(quality, dict):
            return "UNKNOWN", False
        status = str(quality.get("status") or quality.get("state") or "UNKNOWN").upper()
        explicitly_stale = any(quality.get(key) is True for key in ("stale", "is_stale", "stale_state"))
        return status, explicitly_stale

    @classmethod
    def authorization(cls, candidate: dict[str, Any]) -> tuple[bool, str]:
        """Validate the persisted admission marker without recreating a decision."""
        metadata: dict[str, Any] = {}
        features = candidate.get("features")
        if isinstance(features, dict):
            metadata.update(features)
        raw_features = candidate.get("features_json")
        if raw_features:
            try:
                parsed = json.loads(raw_features) if isinstance(raw_features, str) else raw_features
            except (TypeError, ValueError, json.JSONDecodeError):
                parsed = {}
            if isinstance(parsed, dict):
                metadata.update(parsed)
        metadata.update(candidate)
        if metadata.get("decision_authority") != cls.AUTHORITY:
            return False, "DECISION_AUTHORITY_MISSING"
        if metadata.get("decision_version") != cls.DECISION_VERSION:
            return False, "DECISION_VERSION_UNSUPPORTED"
        if str(metadata.get("decision_source") or "").upper() in {"", "UNSPECIFIED"}:
            return False, "DECISION_SOURCE_MISSING"
        path = metadata.get("decision_path")
        if not isinstance(path, list) or not path or path[-1] != cls.AUTHORITY:
            return False, "DECISION_PATH_INVALID"
        if not metadata.get("decision_timestamp"):
            return False, "DECISION_TIMESTAMP_MISSING"
        if metadata.get("decision_outcome") != cls.APPROVED:
            return False, "DECISION_NOT_APPROVED"
        if metadata.get("decision_gate_passed") is not True:
            return False, "DECISION_GATE_NOT_PASSED"
        direction = str(metadata.get("direction") or metadata.get("side") or "").upper()
        if direction not in {"LONG", "SHORT"}:
            return False, "DECISION_DIRECTION_INVALID"
        return True, "APPROVED"

    def enrich(self, data: dict[str, Any], *, source: str | None = None) -> dict[str, Any]:
        data = dict(data)
        data["reasons"] = self._dedupe_text(data.get("reasons"))
        data["triggers"] = self._dedupe_text(data.get("triggers"))
        components = self._dedupe_components(data.get("score_components"))
        data["score_components"] = components

        positives = sorted((c for c in components if float(c.get("value") or 0) > 0), key=lambda c: float(c.get("value") or 0), reverse=True)
        negatives = sorted((c for c in components if float(c.get("value") or 0) < 0), key=lambda c: float(c.get("value") or 0))
        data["strongest_drivers"] = positives[:4]
        data["biggest_blockers"] = negatives[:4]

        direction_score = float(data.get("direction_score") or 0)
        setup_score = float(data.get("setup_score", data.get("score")) or 0)
        status = str(data.get("execution_status") or "🔵 WATCHLIST")
        hard_block = any(str(x).startswith("⛔") for x in data.get("reasons") or [])
        regime = str((data.get("market_regime") or {}).get("code") or "UNKNOWN")
        direction = str(data.get("direction") or "NO_TRADE").upper()
        data_quality, explicitly_stale = self._data_quality(data)

        vetoes: list[str] = []
        if direction not in {"LONG", "SHORT"}:
            vetoes.append("INVALID_OR_NO_TRADE_DIRECTION")
        if status not in self.EXECUTABLE:
            vetoes.append("STATUS_NOT_EXECUTABLE")
        if direction_score < 62:
            vetoes.append("DIRECTION_SCORE_BELOW_GATE")
        if setup_score < 62:
            vetoes.append("SETUP_SCORE_BELOW_GATE")
        if hard_block:
            vetoes.append("HARD_BLOCK_REASON")
        if regime in {"RANGING", "COMPRESSION"}:
            vetoes.append(f"REGIME_{regime}")
        if not bool(data.get("plan_valid", True)):
            vetoes.append("PLAN_INVALID")
        if data_quality in self.INVALID_DATA_STATES or explicitly_stale:
            vetoes.append("DATA_QUALITY_STALE" if explicitly_stale else f"DATA_QUALITY_{data_quality}")

        # Decision gate: weak/no-edge ideas remain observations and cannot be
        # promoted into a full executable trade merely because geometry exists.
        actionable = not vetoes
        data["decision_gate_passed"] = actionable
        data["decision_authority"] = self.AUTHORITY
        data["decision_version"] = self.DECISION_VERSION
        data["decision_source"] = str(source or data.get("decision_source") or data.get("analysis_source") or "UNSPECIFIED").upper()
        data["decision_outcome"] = self.APPROVED if actionable else self.NO_TRADE
        data["decision_veto_reasons"] = vetoes
        data["decision_path"] = [
            "Analyzer", "TradePlanIntegrity", "ProbabilityEngine", "DecisionQualityEngine"
        ]
        data["decision_timestamp"] = str(
            data.get("timestamp") or data.get("captured_at") or datetime.now(timezone.utc).isoformat()
        )
        data["decision_data_quality"] = data_quality
        data["plan_mode"] = "TRADE_PLAN" if actionable else "AREA_OF_INTEREST"
        if not actionable and status == "🟢 READY":
            data["execution_status"] = "🔵 WATCHLIST"
            data["recommendation"] = "OBSERVE / NO EXECUTION EDGE"

        if setup_score >= 82:
            stars = "⭐⭐⭐⭐⭐"
        elif setup_score >= 72:
            stars = "⭐⭐⭐⭐☆"
        elif setup_score >= 62:
            stars = "⭐⭐⭐☆☆"
        elif setup_score >= 52:
            stars = "⭐⭐☆☆☆"
        else:
            stars = "⭐☆☆☆☆"
        data["trade_quality_stars"] = stars
        data["decision_action"] = (
            "EXECUTE" if actionable and status == "🟢 READY" else
            "WAIT" if actionable else
            "WATCH" if setup_score >= 52 and direction_score >= 52 else
            "SKIP"
        )
        data["would_take_trade"] = bool(actionable and status == "🟢 READY" and setup_score >= 72)
        data["entry_reasons"] = self._entry_reasons(data)
        data["expected_path"] = self._expected_path(data)
        data["why_trade_exists"] = self._why_exists(data)
        data["conviction"] = ConvictionEngine().evaluate(data)
        data["system_decision"] = data["conviction"]["action"]
        data["unified_decision"] = self.unified.evaluate(data)
        symbol = str(data.get("symbol") or "UNKNOWN")
        timeframe = str(data.get("timeframe") or "1h")
        data["market_memory"] = self.memory.remember(symbol, timeframe, data)
        data["decision_brain"] = self.brain.evaluate(data)
        data["expected_value"] = data["decision_brain"]["expected_value"]
        data["system_decision"] = data["decision_brain"]["action"]
        return data
