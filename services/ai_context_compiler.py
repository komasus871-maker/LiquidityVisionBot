from __future__ import annotations

import json
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, Mapping


COMPILER_VERSION = "ai-context-compiler-v1"
FEATURE_VERSION = "ai-decision-context-v1"


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False, default=str)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _pick(value: Any, *keys: str) -> dict[str, Any]:
    source = _mapping(value)
    return {key: source[key] for key in keys if key in source and source[key] is not None}


def _bounded(items: Any, limit: int) -> list[Any]:
    return list(items)[:limit] if isinstance(items, (list, tuple)) else []


@dataclass(frozen=True, slots=True)
class CompiledAIContext:
    payload: dict[str, Any]
    original_chars: int
    compiled_chars: int
    max_chars: int
    sections_included: tuple[str, ...]
    sections_omitted: tuple[str, ...]
    compiler_version: str = COMPILER_VERSION
    feature_version: str = FEATURE_VERSION

    @property
    def budget_utilization(self) -> float:
        return round(self.compiled_chars / max(1, self.max_chars), 6)

    @property
    def fits_budget(self) -> bool:
        return self.compiled_chars <= self.max_chars

    def telemetry(self) -> dict[str, Any]:
        return {
            "compiler_version": self.compiler_version,
            "feature_version": self.feature_version,
            "original_chars": self.original_chars,
            "compiled_chars": self.compiled_chars,
            "max_chars": self.max_chars,
            "budget_utilization": self.budget_utilization,
            "sections_included": list(self.sections_included),
            "sections_omitted": list(self.sections_omitted),
        }


class AIContextCompiler:
    """Deterministically projects internal state into decision-useful provider context."""

    def __init__(self, max_chars: int) -> None:
        self.max_chars = max(1000, int(max_chars))

    @staticmethod
    def _source(context: Any) -> dict[str, Any]:
        if is_dataclass(context):
            return asdict(context)
        return _mapping(context)

    @staticmethod
    def _intelligence(features: Mapping[str, Any]) -> dict[str, Any]:
        for key in ("market_intelligence_v3", "market_intelligence_v2", "market_intelligence"):
            value = features.get(key)
            if isinstance(value, Mapping):
                return dict(value)
        return {}

    def compile(self, context: Any) -> CompiledAIContext:
        source = self._source(context)
        original = _canonical(source)
        market = _mapping(source.get("market"))
        deterministic = _mapping(source.get("deterministic"))
        features = _mapping(source.get("features"))
        intelligence = self._intelligence(features)
        story = _mapping(intelligence.get("market_story"))
        structure = _mapping(intelligence.get("structure_quality"))
        quality = _mapping(intelligence.get("quality") or intelligence.get("signal_quality_v3"))
        liquidity = _mapping(intelligence.get("liquidity") or intelligence.get("liquidity_map"))
        micro = _mapping(intelligence.get("microstructure"))
        derivatives = _mapping(intelligence.get("funding_open_interest"))
        benchmark = _mapping(intelligence.get("relative_strength"))
        momentum = _mapping(intelligence.get("momentum"))
        trend = _mapping(intelligence.get("trend_maturity"))

        mandatory = {
            "identity": {
                "signal_id": source.get("signal_id"), "symbol": source.get("symbol"),
                "timeframe": source.get("timeframe"),
                "direction": deterministic.get("direction"),
                "timestamp": source.get("market_timestamp"),
                "strategy_candidate": deterministic.get("setup_family"),
                "status": deterministic.get("status"),
            },
            "market": _pick(market, "price", "entry", "stop", "take_profits", "expected_rr",
                            "price_unit", "percentage_unit"),
            "market_story": {"state": story.get("state"), "transition": story.get("transition"),
                             "facts": _bounded(story.get("facts"), 4)},
            "structure": _pick(structure, "direction", "break", "sweep", "quality",
                               "close_confirmed", "reason_codes"),
            "major_liquidity": {
                "likely_attractor": liquidity.get("likely_attractor"),
                "above": _bounded(liquidity.get("above"), 2),
                "below": _bounded(liquidity.get("below"), 2),
            },
            "invalidation": quality.get("invalidation") or {
                "stop": market.get("stop"), "status": "NOT_ENRICHED"},
            "target_geometry": {"take_profits": market.get("take_profits"),
                                "expected_rr": market.get("expected_rr"),
                                "quality": (_mapping(quality.get("family_scores"))).get("TARGET_REALISM")},
            "strongest_contradictions": _bounded(quality.get("contradicting_evidence"), 4),
            "data_availability": {
                "uncertainties": _bounded(quality.get("uncertainties"), 12),
                "microstructure": micro.get("status", "UNAVAILABLE"),
                "derivatives": derivatives.get("status", "UNAVAILABLE"),
                "benchmark": benchmark.get("status", "UNAVAILABLE"),
            },
            "deterministic_summary": _pick(deterministic, "recommendation", "confidence",
                                           "bull_score", "bear_score"),
            "compiler": {"version": COMPILER_VERSION, "feature_version": FEATURE_VERSION},
        }
        payload: dict[str, Any] = {"tier_1_mandatory": mandatory}
        included = ["tier_1_mandatory"]
        omitted: list[str] = []

        tier_2 = {
            "hierarchy": intelligence.get("hierarchy") or features.get("multi_timeframe"),
            "momentum": _pick(momentum, "state", "direction", "score", "acceleration", "exhaustion"),
            "volatility_trend": _pick(trend, "state", "direction", "distance_atr", "momentum_aligned"),
            "microstructure": _pick(micro, "status", "freshness", "spread_bps", "depth_bands",
                                    "behavior_labels", "interaction_quality"),
            "derivatives": _pick(derivatives, "status", "funding_rate", "funding_sign",
                                 "open_interest", "open_interest_change_pct", "price_oi_state", "freshness"),
            "benchmark": _pick(benchmark, "status", "state", "relative_move_pct", "quality"),
            "evidence_families": _mapping(quality.get("family_scores")),
            "quality_v3": _pick(quality, "overall_quality", "market_quality",
                                "evidence_family_count", "evidence_diversity_score",
                                "contradiction_penalty", "critical_disqualifiers"),
            "entry_readiness": intelligence.get("entry_readiness"),
            "strategy_suitability": intelligence.get("strategy_suitability"),
            "portfolio_exposure": {
                "count": _mapping(source.get("portfolio")).get("count", 0),
                "positions": [_pick(item, "symbol", "side", "status") for item in
                              _bounded(_mapping(source.get("portfolio")).get("open_positions"), 5)],
            },
        }
        self._add_if_fits(payload, "tier_2_high_value", tier_2, included, omitted)

        history = _mapping(source.get("history"))
        tier_3 = {
            "similar_trades": [_pick(item, "result", "realized_r", "setup_key")
                               for item in _bounded(history.get("similar_trades"), 3)],
            "prior_ai": [_pick(item, "recommended_action", "opportunity_quality", "direction_correct")
                         for item in _bounded(history.get("prior_ai_decisions"), 3)],
            "learned_patterns": _pick(_mapping(history.get("learned_patterns")),
                                      "snapshot_key", "sample_size"),
        }
        self._add_if_fits(payload, "tier_3_conditional", tier_3, included, omitted)

        compiled = _canonical(payload)
        if len(compiled) > self.max_chars:
            payload = self._emergency_compact(mandatory)
            included = ["tier_1_mandatory"]
            omitted = sorted(set(omitted + ["tier_2_high_value", "tier_3_conditional",
                                            "verbose_explanations", "large_arrays"]))
            compiled = _canonical(payload)
        return CompiledAIContext(
            payload=payload, original_chars=len(original), compiled_chars=len(compiled),
            max_chars=self.max_chars, sections_included=tuple(included),
            sections_omitted=tuple(sorted(set(omitted + ["tier_4_verbose_internal_state"]))),
        )

    def _add_if_fits(self, payload: dict[str, Any], name: str, section: dict[str, Any],
                     included: list[str], omitted: list[str]) -> None:
        candidate = {**payload, name: section}
        if len(_canonical(candidate)) <= self.max_chars:
            payload[name] = section
            included.append(name)
        else:
            omitted.append(name)

    @staticmethod
    def _emergency_compact(mandatory: dict[str, Any]) -> dict[str, Any]:
        compact = dict(mandatory)
        compact["market_story"] = _pick(_mapping(mandatory.get("market_story")), "state", "transition")
        compact["major_liquidity"] = {
            "likely_attractor": _mapping(mandatory.get("major_liquidity")).get("likely_attractor")}
        compact["strongest_contradictions"] = _bounded(mandatory.get("strongest_contradictions"), 2)
        return {"tier_1_mandatory": compact}
