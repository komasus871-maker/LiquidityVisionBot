from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from database.database import connect
from services.market_intelligence import (
    INTELLIGENCE_VERSION,
    MICROSTRUCTURE_VERSION,
    QUALITY_VERSION,
    RANK_VERSION,
    contains_raw_order_book,
)


FORBIDDEN_DECISION_KEYS = {
    "closed_at", "exit_price", "future_price", "max_drawdown_pct", "max_profit_pct",
    "outcome", "realized_pnl", "realized_r", "result", "stop_hit_at", "tp1_hit_at",
    "tp2_hit_at", "tp3_hit_at",
}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False, default=str)


def _loads(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError, OverflowError):
        return default


def _checksum(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _contains_forbidden(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if normalized in FORBIDDEN_DECISION_KEYS:
                found.add(normalized)
            found.update(_contains_forbidden(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_contains_forbidden(child))
    return found


class MarketIntelligenceRepository:
    def strategy_distribution(self, telegram_id: int, limit: int = 500) -> dict[str, Any]:
        safe = max(1, min(int(limit), 2000))
        with connect() as conn:
            rows = conn.execute(f"""SELECT strategy_suitability_json FROM market_intelligence_snapshots
                WHERE (owner_telegram_id=? OR owner_telegram_id IS NULL)
                ORDER BY id DESC LIMIT {safe}""", (telegram_id,)).fetchall()
        counts: dict[str, int] = {}
        margins: list[float] = []
        for row in rows:
            scores = _loads(row["strategy_suitability_json"], {})
            ranked = sorted(((str(key), float(value)) for key, value in scores.items()),
                            key=lambda item: (-item[1], item[0]))
            if not ranked:
                continue
            counts[ranked[0][0]] = counts.get(ranked[0][0], 0) + 1
            if len(ranked) > 1:
                margins.append(ranked[0][1] - ranked[1][1])
        return {"snapshots": len(rows), "classified": sum(counts.values()),
                "distribution": sorted(counts.items(), key=lambda item: (-item[1], item[0])),
                "average_top_margin": sum(margins) / len(margins) if margins else None,
                "forced_diversity": False, "execution_authority": False}

    """Persistence and read APIs for immutable, advisory market-intelligence rows."""

    @staticmethod
    def extract(features: Mapping[str, Any]) -> dict[str, Any] | None:
        direct = features.get("market_intelligence")
        if isinstance(direct, dict):
            return direct
        extras = features.get("extras")
        if isinstance(extras, dict) and isinstance(extras.get("market_intelligence"), dict):
            return extras["market_intelligence"]
        return None

    def persist_signal(self, research_snapshot: Mapping[str, Any], features: Mapping[str, Any]) -> dict[str, Any] | None:
        intelligence = self.extract(features)
        if not intelligence or intelligence.get("version") != INTELLIGENCE_VERSION:
            return None
        forbidden = _contains_forbidden(intelligence)
        if forbidden:
            raise ValueError(f"market intelligence contains future/outcome keys: {','.join(sorted(forbidden))}")
        snapshot = json.loads(_canonical(intelligence))
        story = snapshot.get("market_story") if isinstance(snapshot.get("market_story"), dict) else {}
        with connect() as conn:
            previous = conn.execute("""SELECT full_snapshot_json FROM market_intelligence_snapshots
                WHERE symbol=? AND timeframe=? AND decision_at<? ORDER BY decision_at DESC,id DESC LIMIT 1""",
                (research_snapshot["symbol"], research_snapshot["timeframe"], research_snapshot["decision_at"])).fetchone()
        if previous and story.get("previous_state") in {None, "", "UNKNOWN"}:
            previous_snapshot = _loads(previous["full_snapshot_json"], {})
            previous_state = str((previous_snapshot.get("market_story") or {}).get("state") or "UNKNOWN")
            story["previous_state"] = previous_state
            story["transition"] = (f"{previous_state}->{story.get('state')}"
                                     if previous_state != story.get("state") else "UNCHANGED")
        snapshot["market_story"] = story
        snapshot_checksum = _checksum(snapshot)
        quality = snapshot.get("signal_quality_v3") or snapshot.get("signal_quality_v2") or {}
        reversal = snapshot.get("reversal_research") or {}
        market_quality = _number(quality.get("market_quality"), 0) or 0
        overall_quality = _number(quality.get("overall_quality"), 0) or 0
        diversity = _number(quality.get("evidence_diversity_score"), 0) or 0
        contradiction_count = len(quality.get("contradicting_evidence") or [])
        critical_count = len(quality.get("critical_disqualifiers") or [])
        now = datetime.now(timezone.utc).isoformat()
        values = (
            research_snapshot["snapshot_id"], research_snapshot["signal_id"],
            research_snapshot.get("owner_telegram_id"), research_snapshot["symbol"],
            research_snapshot["timeframe"], research_snapshot["side"],
            research_snapshot["decision_at"], INTELLIGENCE_VERSION,
            str(story.get("version") or "market-story-v1"), QUALITY_VERSION,
            snapshot_checksum, str(story.get("state") or "UNKNOWN"),
            market_quality, overall_quality, diversity, contradiction_count, critical_count,
            _canonical(story), _canonical(snapshot.get("level_map") or {}),
            _canonical(snapshot.get("liquidity_map") or {}),
            _canonical(snapshot.get("microstructure") or {}),
            _canonical(reversal), _canonical(quality),
            _canonical(snapshot.get("strategy_suitability") or {}),
            _canonical(snapshot.get("research_policies") or {}), _canonical(snapshot), now,
        )
        with connect() as conn:
            conn.execute("""INSERT INTO market_intelligence_snapshots(
                research_snapshot_id,signal_id,owner_telegram_id,symbol,timeframe,side,decision_at,
                intelligence_version,story_version,quality_version,snapshot_checksum,story_state,
                market_quality,overall_quality,evidence_diversity,contradiction_count,critical_count,
                story_json,level_map_json,liquidity_map_json,microstructure_json,reversal_json,quality_json,
                strategy_suitability_json,research_policies_json,full_snapshot_json,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(signal_id,intelligence_version) DO NOTHING""", values)
            conn.execute("""INSERT INTO research_signal_rankings(snapshot_id,signal_id,rank_version,
                diagnostic_score,components_json,created_at) VALUES(?,?,?,?,?,?)
                ON CONFLICT(snapshot_id,rank_version) DO NOTHING""", (
                research_snapshot["snapshot_id"], research_snapshot["signal_id"], RANK_VERSION,
                overall_quality, _canonical({
                    "market_quality": market_quality,
                    "family_scores": quality.get("family_scores") or {},
                    "strongest_advantages": sorted(
                        (quality.get("family_scores") or {}).items(),
                        key=lambda item: (-_number(item[1], 0), str(item[0])),
                    )[:3],
                    "strongest_weaknesses": sorted(
                        (quality.get("family_scores") or {}).items(),
                        key=lambda item: (_number(item[1], 0), str(item[0])),
                    )[:3],
                    "contradictions": quality.get("contradicting_evidence") or [],
                    "uncertainties": quality.get("uncertainties") or [],
                    "critical_disqualifiers": quality.get("critical_disqualifiers") or [],
                    "contradiction_penalty": quality.get("contradiction_penalty") or 0,
                    "evidence_diversity": diversity,
                    "score_is_probability": False,
                    "economic_authority": False,
                }), now))
            row = conn.execute("""SELECT * FROM market_intelligence_snapshots
                WHERE signal_id=? AND intelligence_version=?""",
                (research_snapshot["signal_id"], INTELLIGENCE_VERSION)).fetchone()
        return self._decode(dict(row)) if row else None

    @staticmethod
    def _decode(row: dict[str, Any]) -> dict[str, Any]:
        for key in ("story_json", "level_map_json", "liquidity_map_json", "microstructure_json",
                    "reversal_json", "quality_json", "strategy_suitability_json",
                    "research_policies_json", "full_snapshot_json", "aggregate_json"):
            if key in row:
                row[key.removesuffix("_json")] = _loads(row.get(key), {})
        return row

    def get_signal(self, signal_id: int, owner_telegram_id: int | None = None) -> dict[str, Any] | None:
        with connect() as conn:
            row = conn.execute("""SELECT * FROM market_intelligence_snapshots
                WHERE signal_id=? AND (? IS NULL OR owner_telegram_id IS NULL OR owner_telegram_id=0
                    OR owner_telegram_id=?) ORDER BY id DESC LIMIT 1""",
                (signal_id, owner_telegram_id, owner_telegram_id)).fetchone()
        return self._decode(dict(row)) if row else None

    def latest_symbol(self, symbol: str, owner_telegram_id: int | None = None) -> dict[str, Any] | None:
        normalized = str(symbol).upper().replace("-", "")
        with connect() as conn:
            row = conn.execute("""SELECT * FROM market_intelligence_snapshots
                WHERE REPLACE(UPPER(symbol),'-','')=? AND
                    (? IS NULL OR owner_telegram_id IS NULL OR owner_telegram_id=0 OR owner_telegram_id=?)
                ORDER BY decision_at DESC,id DESC LIMIT 1""", (normalized, owner_telegram_id, owner_telegram_id)).fetchone()
        return self._decode(dict(row)) if row else None

    def recent_reversals(self, owner_telegram_id: int | None = None, limit: int = 20) -> list[dict[str, Any]]:
        safe = max(1, min(int(limit), 100))
        with connect() as conn:
            rows = conn.execute("""SELECT * FROM market_intelligence_snapshots
                WHERE (? IS NULL OR owner_telegram_id IS NULL OR owner_telegram_id=0 OR owner_telegram_id=?)
                ORDER BY decision_at DESC,id DESC LIMIT ?""", (owner_telegram_id, owner_telegram_id, safe * 5)).fetchall()
        result = []
        for raw in rows:
            row = self._decode(dict(raw))
            candidates = row.get("reversal") or {}
            if any(str(candidate.get("state") or "").endswith(("_EARLY", "_CONFIRMED", "_CONTINUATION_RISK"))
                   for candidate in candidates.values() if isinstance(candidate, dict)):
                result.append(row)
            if len(result) >= safe:
                break
        return result

    def persist_microstructure(self, *, symbol: str, exchange: str, environment: str,
                               aggregate: Mapping[str, Any], sampled_at: str | None = None,
                               ttl_seconds: int | None = None) -> bool:
        if aggregate.get("version") != MICROSTRUCTURE_VERSION:
            raise ValueError("microstructure aggregate version is unsupported")
        if aggregate.get("raw_book_persisted") or contains_raw_order_book(aggregate):
            raise ValueError("raw order books must not be persisted")
        sample_count = int(aggregate.get("sample_count") or 0)
        if sample_count < 1 or sample_count > 60:
            raise ValueError("microstructure sample count is outside the bounded range")
        serialized_aggregate = _canonical(aggregate)
        if len(serialized_aggregate.encode("utf-8")) > 262_144:
            raise ValueError("microstructure aggregate exceeds the persistence size bound")
        sampled = sampled_at or datetime.now(timezone.utc).isoformat()
        ttl = max(30, min(int(ttl_seconds or 300), 3600))
        parsed = datetime.fromisoformat(sampled.replace("Z", "+00:00"))
        parsed = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        expires = (parsed + timedelta(seconds=ttl)).isoformat()
        identity = {"symbol": str(symbol).upper(), "exchange": str(exchange).lower(),
                    "environment": environment, "sampled_at": sampled, "aggregate": aggregate}
        checksum = _checksum(identity)
        with connect() as conn:
            cur = conn.execute("""INSERT INTO microstructure_aggregates(symbol,exchange,environment,
                aggregate_version,aggregate_checksum,sample_count,interaction_quality,status,
                aggregate_json,sampled_at,expires_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(aggregate_checksum) DO NOTHING""", (
                identity["symbol"], identity["exchange"], environment, MICROSTRUCTURE_VERSION,
                checksum, sample_count,
                _number(aggregate.get("interaction_quality"), 0) or 0,
                str(aggregate.get("status") or "UNKNOWN"), serialized_aggregate, sampled, expires,
                datetime.now(timezone.utc).isoformat()))
        return cur.rowcount == 1

    def latest_microstructure(self, symbol: str, as_of: str | None = None) -> dict[str, Any] | None:
        normalized = str(symbol).upper().replace("-", "")
        cutoff = as_of or datetime.now(timezone.utc).isoformat()
        with connect() as conn:
            row = conn.execute("""SELECT * FROM microstructure_aggregates
                WHERE REPLACE(UPPER(symbol),'-','')=? AND sampled_at<=?
                ORDER BY sampled_at DESC,id DESC LIMIT 1""",
                (normalized, cutoff)).fetchone()
        if not row:
            return None
        result = self._decode(dict(row))
        try:
            expires = datetime.fromisoformat(str(result["expires_at"]).replace("Z", "+00:00"))
            expires = expires if expires.tzinfo else expires.replace(tzinfo=timezone.utc)
            result["stale"] = expires < datetime.now(timezone.utc)
        except (TypeError, ValueError):
            result["stale"] = True
        return result

    def data_health(self, symbol: str | None = None,
                    owner_telegram_id: int | None = None) -> dict[str, Any]:
        """Return bounded availability, never inferred values, for operator diagnostics."""
        market = self.latest_symbol(symbol, owner_telegram_id) if symbol else None
        micro = self.latest_microstructure(symbol) if symbol else None
        snapshot = (market or {}).get("full_snapshot") or {}
        derivatives = ((micro or {}).get("aggregate") or {}).get("funding_open_interest") or {}

        micro_enabled = os.getenv("MICROSTRUCTURE_COLLECTION_ENABLED", "false").strip().lower() in {
            "1", "true", "yes", "on"}

        def state(available: bool, *, stale: bool = False, insufficient: bool = False,
                  missing: str = "UNAVAILABLE") -> str:
            if stale:
                return "STALE"
            if available and insufficient:
                return "INSUFFICIENT_HISTORY"
            return "AVAILABLE" if available else missing

        funding_available = derivatives.get("funding_rate") is not None
        oi_available = derivatives.get("open_interest") is not None
        return {
            "version": "data-availability-v2", "symbol": symbol,
            "candles": state(bool(market), missing="DECISION_SNAPSHOT_MISSING"),
            "benchmark": state((snapshot.get("relative_strength") or {}).get("status") == "AVAILABLE",
                               missing="SYNCHRONIZED_FRAME_MISSING"),
            "funding": state(funding_available, stale=bool(micro and micro.get("stale")),
                             missing=("ENDPOINT_UNAVAILABLE" if micro else
                                      "WAITING_FOR_FIRST_SAMPLE" if micro_enabled else
                                      "DISABLED_BY_CONFIGURATION")),
            "open_interest": state(oi_available, stale=bool(micro and micro.get("stale")),
                                   insufficient=oi_available and derivatives.get("open_interest_change_pct") is None,
                                   missing=("ENDPOINT_UNAVAILABLE" if micro else
                                            "WAITING_FOR_FIRST_SAMPLE" if micro_enabled else
                                            "DISABLED_BY_CONFIGURATION")),
            "microstructure": state(bool(micro), stale=bool(micro and micro.get("stale")),
                                    missing="WAITING_FOR_FIRST_SAMPLE" if micro_enabled else
                                    "DISABLED_BY_CONFIGURATION"),
            "liquidity_map": state(bool(snapshot.get("liquidity_map")),
                                   missing="DECISION_SNAPSHOT_MISSING"),
            "ordered_path": "INSUFFICIENT_HISTORY",
            "execution_costs": state(bool((snapshot.get("research_policies") or {}).get(
                "estimated_roundtrip_cost_pct") is not None), missing="DECISION_SNAPSHOT_MISSING"),
            "remediation": {
                "microstructure": ("Set MICROSTRUCTURE_COLLECTION_ENABLED=true and redeploy the Render service."
                                   if not micro_enabled else "Wait for a successful observer cycle and inspect /system_health."),
                "benchmark": "Supply a synchronized BTC decision-time frame; never backfill with future candles.",
                "ordered_path": "Accumulate trustworthy ordered-path observations.",
            },
            "funding_context": derivatives if funding_available else {},
            "open_interest_context": derivatives if oi_available else {},
            "automatic_policy_change": False,
        }

    def quality_threshold_report(self, owner_telegram_id: int | None = None) -> dict[str, Any]:
        with connect() as conn:
            rows = [dict(row) for row in conn.execute("""SELECT m.overall_quality,m.signal_id,
                o.outcome_json FROM market_intelligence_snapshots m
                JOIN research_signal_snapshots r ON r.snapshot_id=m.research_snapshot_id
                LEFT JOIN research_outcomes o ON o.snapshot_id=m.research_snapshot_id AND o.id=(
                    SELECT MAX(o2.id) FROM research_outcomes o2 WHERE o2.snapshot_id=m.research_snapshot_id)
                WHERE r.capture_quality='DECISION_TIME' AND
                    (? IS NULL OR m.owner_telegram_id IS NULL OR m.owner_telegram_id=0
                    OR m.owner_telegram_id=?) ORDER BY m.decision_at,m.id""",
                (owner_telegram_id, owner_telegram_id)).fetchall()]
        resolved = []
        for row in rows:
            outcome = _loads(row.get("outcome_json"), {})
            pure = outcome.get("pure_market") if isinstance(outcome.get("pure_market"), dict) else {}
            value = pure.get("signal_r")
            if pure.get("eligible") and value is not None:
                resolved.append((float(row["overall_quality"]), float(value)))
        curves = []
        for threshold in range(50, 91, 5):
            accepted = [value for quality, value in resolved if quality >= threshold]
            rejected = [value for quality, value in resolved if quality < threshold]
            equity = peak = drawdown = 0.0
            for value in accepted:
                equity += value
                peak = max(peak, equity)
                drawdown = min(drawdown, equity - peak)
            curves.append({"threshold": threshold, "trades": len(accepted),
                           "winners": sum(value > 0 for value in accepted),
                           "losers": sum(value < 0 for value in accepted),
                           "expectancy_r": sum(accepted) / len(accepted) if accepted else None,
                           "missed_winners": sum(value > 0 for value in rejected),
                           "avoided_losses": sum(value < 0 for value in rejected),
                           "max_drawdown_proxy_r": drawdown if accepted else None})
        return {"version": QUALITY_VERSION, "snapshots": len(rows), "resolved_samples": len(resolved),
                "threshold_curves": curves, "minimum_sample_gate": 20,
                "status": "SUFFICIENT" if len(resolved) >= 20 else "INSUFFICIENT_SAMPLES",
                "automatic_filter_change": False, "profitability_claim": False}

    def quality_exception_cohorts(self, owner_telegram_id: int | None = None) -> dict[str, Any]:
        with connect() as conn:
            rows = [dict(row) for row in conn.execute("""SELECT m.signal_id,m.overall_quality,
                m.quality_json,m.reversal_json,s.confidence,o.outcome_json
                FROM market_intelligence_snapshots m JOIN signals s ON s.id=m.signal_id
                JOIN research_signal_snapshots r ON r.snapshot_id=m.research_snapshot_id
                LEFT JOIN research_outcomes o ON o.snapshot_id=m.research_snapshot_id AND o.id=(
                    SELECT MAX(o2.id) FROM research_outcomes o2 WHERE o2.snapshot_id=m.research_snapshot_id)
                WHERE r.capture_quality='DECISION_TIME' AND
                (? IS NULL OR m.owner_telegram_id IS NULL OR m.owner_telegram_id=0 OR m.owner_telegram_id=?)""",
                (owner_telegram_id, owner_telegram_id)).fetchall()]
        cohorts = {name: [] for name in (
            "LOW_QUALITY_WINNER", "HIGH_QUALITY_LOSER", "MISSED_EXPLOSIVE_CONTINUATION",
            "SUCCESSFUL_EXHAUSTION_REVERSAL", "FAILED_EXHAUSTION_REVERSAL",
            "LATE_ENTRY_WINNER", "HIGH_CONFIDENCE_LOSER")}
        for row in rows:
            outcome = _loads(row.get("outcome_json"), {})
            pure = outcome.get("pure_market") if isinstance(outcome.get("pure_market"), dict) else {}
            if not pure.get("eligible") or pure.get("signal_r") is None:
                continue
            r_value, quality = float(pure["signal_r"]), float(row.get("overall_quality") or 0)
            confidence = float(row.get("confidence") or 0)
            contradictions = " ".join(str(item).lower() for item in
                                      _loads(row.get("quality_json"), {}).get("contradicting_evidence") or [])
            reversals = _loads(row.get("reversal_json"), {})
            exhaustion = any("CONFIRMED" in str(item.get("state") or "")
                             for item in reversals.values() if isinstance(item, dict))
            matches = {
                "LOW_QUALITY_WINNER": quality < 50 and r_value > 0,
                "HIGH_QUALITY_LOSER": quality >= 70 and r_value < 0,
                "MISSED_EXPLOSIVE_CONTINUATION": quality < 60 and r_value >= 3,
                "SUCCESSFUL_EXHAUSTION_REVERSAL": exhaustion and r_value > 0,
                "FAILED_EXHAUSTION_REVERSAL": exhaustion and r_value <= 0,
                "LATE_ENTRY_WINNER": "late" in contradictions and r_value > 0,
                "HIGH_CONFIDENCE_LOSER": confidence >= 80 and r_value < 0,
            }
            for name, matched in matches.items():
                if matched:
                    cohorts[name].append((int(row["signal_id"]), r_value))
        return {"cohorts": [{"name": name, "samples": len(values),
                              "average_r": sum(value for _, value in values) / len(values) if values else None,
                              "example_signal_ids": [signal_id for signal_id, _ in values[:5]]}
                             for name, values in cohorts.items()],
                "automatic_policy_change": False, "profitability_claim": False}

    def policy_report(self, policy: str, owner_telegram_id: int | None = None) -> dict[str, Any]:
        normalized = str(policy).upper()
        with connect() as conn:
            rows = [dict(row) for row in conn.execute("""SELECT m.research_policies_json,
                m.overall_quality,o.outcome_json FROM market_intelligence_snapshots m
                JOIN research_signal_snapshots r ON r.snapshot_id=m.research_snapshot_id
                LEFT JOIN research_outcomes o ON o.snapshot_id=m.research_snapshot_id AND o.id=(
                    SELECT MAX(o2.id) FROM research_outcomes o2 WHERE o2.snapshot_id=m.research_snapshot_id)
                WHERE r.capture_quality='DECISION_TIME' AND
                    (? IS NULL OR m.owner_telegram_id IS NULL OR m.owner_telegram_id=0
                    OR m.owner_telegram_id=?) ORDER BY m.decision_at""",
                (owner_telegram_id, owner_telegram_id)).fetchall()]
        resolved = sum(bool(_loads(row.get("outcome_json"), {})) for row in rows)
        payload = {
            "policy": normalized, "decision_snapshots": len(rows), "resolved_outcomes": resolved,
            "status": "COLLECTING_DECISION_TIME_DATA" if len(rows) < 20 else "READY_FOR_FORMAL_EVALUATION",
            "mode": "PAPER_SHADOW_ONLY", "automatic_policy_change": False,
            "missed_winners_included": True, "avoided_losses_included": True,
        }
        if normalized == "RR":
            bands: dict[str, int] = {}
            for row in rows:
                planned = _loads(row.get("research_policies_json"), {}).get("planned_rr")
                key = "unknown" if planned is None else f"{float(planned):.1f}R"
                bands[key] = bands.get(key, 0) + 1
            payload["planned_rr_distribution"] = bands
            payload["candidate_bands"] = [1, 1.5, 2, 2.5, 3]
        elif normalized == "ENTRY":
            payload["policies"] = ["MARKET_NOW", "CONFIRMATION_CLOSE", "RETEST", "FVG", "ORDER_BLOCK", "LIQUIDITY_RECLAIM"]
            payload["ordered_path_required"] = True
        elif normalized == "REENTRY":
            payload["maximum_attempts"] = 2
            payload["martingale"] = False
            payload["requires_new_evidence"] = True
        return payload
