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


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


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
    PIPELINE_STAGES = frozenset({
        "request_attempted", "http_success", "payload_valid", "rows_valid",
        "normalized", "aggregate_created", "persist_attempted", "persist_success",
    })

    def record_pipeline_stage(self, *, symbol: str, source_type: str, provider: str,
                              stage: str, success: bool = True,
                              rejection_code: str | None = None) -> dict[str, Any]:
        """Persist normalized provider-stage evidence without retaining provider payloads."""
        stage_key = str(stage).strip().lower()
        if stage_key not in self.PIPELINE_STAGES:
            raise ValueError("MARKET_SOURCE_DIAGNOSTIC_STAGE_INVALID")
        normalized = str(symbol).upper().replace("-", "")
        source_key = str(source_type).upper()
        now = datetime.now(timezone.utc).isoformat()
        code = None if success else str(rejection_code or f"{source_key}_{stage_key.upper()}_FAILED")[:80]
        # stage_key is selected from the fixed allow-list above, never caller SQL.
        with connect() as conn:
            conn.execute(f"""INSERT INTO market_source_diagnostics(symbol,source_type,provider,
                {stage_key},rejection_code,last_success_at,last_failure_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(symbol,source_type,provider) DO UPDATE SET
                    {stage_key}=market_source_diagnostics.{stage_key}+1,
                    rejection_code=CASE WHEN excluded.last_failure_at IS NOT NULL
                        THEN excluded.rejection_code
                        WHEN excluded.last_success_at IS NOT NULL THEN NULL
                        ELSE market_source_diagnostics.rejection_code END,
                    last_success_at=COALESCE(excluded.last_success_at,market_source_diagnostics.last_success_at),
                    last_failure_at=COALESCE(excluded.last_failure_at,market_source_diagnostics.last_failure_at),
                    updated_at=excluded.updated_at""", (
                    normalized, source_key, provider, 1, code,
                    now if success and stage_key == "persist_success" else None,
                    now if not success else None, now))
        return self.pipeline_diagnostics(normalized, source_key, provider) or {}

    def pipeline_diagnostics(self, symbol: str, source_type: str,
                             provider: str | None = None) -> dict[str, Any] | None:
        normalized = str(symbol).upper().replace("-", "")
        source_key = str(source_type).upper()
        with connect() as conn:
            if provider:
                row = conn.execute("""SELECT * FROM market_source_diagnostics
                    WHERE symbol=? AND source_type=? AND provider=?""",
                    (normalized, source_key, provider)).fetchone()
            else:
                row = conn.execute("""SELECT * FROM market_source_diagnostics
                    WHERE symbol=? AND source_type=? ORDER BY updated_at DESC LIMIT 1""",
                    (normalized, source_key)).fetchone()
        if not row:
            return None
        result = dict(row)
        prefix = source_key.lower()
        # Named aliases make operator telemetry match the production runbook.
        return {**result, **{f"{prefix}_{key}": result.get(key) for key in (
            "request_attempted", "http_success", "payload_valid", "rows_valid",
            "normalized", "aggregate_created", "persist_attempted", "persist_success",
            "rejection_code", "last_success_at", "last_failure_at")}}

    def strategy_distribution(self, telegram_id: int, limit: int = 500) -> dict[str, Any]:
        safe = max(1, min(int(limit), 2000))
        with connect() as conn:
            rows = conn.execute(f"""SELECT strategy_suitability_json,full_snapshot_json FROM market_intelligence_snapshots
                WHERE (owner_telegram_id=? OR owner_telegram_id IS NULL)
                ORDER BY id DESC LIMIT {safe}""", (telegram_id,)).fetchall()
        counts: dict[str, int] = {}
        margins: list[float] = []
        classification_states: dict[str, int] = {}
        for row in rows:
            scores = _loads(row["strategy_suitability_json"], {})
            full = _loads(row.get("full_snapshot_json"), {})
            fusion = full.get("strategy_fusion_v2") or {}
            ranked = sorted(((str(key), float(value)) for key, value in scores.items()),
                            key=lambda item: (-item[1], item[0]))
            if not ranked:
                continue
            state = str(fusion.get("fusion_state") or
                        ("TIE" if len(ranked) > 1 and abs(ranked[0][1] - ranked[1][1]) <= .5 else "PRIMARY"))
            classification_states[state] = classification_states.get(state, 0) + 1
            label = "TIE" if state == "TIE" else ranked[0][0]
            counts[label] = counts.get(label, 0) + 1
            if len(ranked) > 1:
                margins.append(ranked[0][1] - ranked[1][1])
        return {"snapshots": len(rows), "classified": sum(counts.values()),
                "distribution": sorted(counts.items(), key=lambda item: (-item[1], item[0])),
                "average_top_margin": sum(margins) / len(margins) if margins else None,
                "classification_states": classification_states,
                "exact_ties_are_not_assigned_alphabetically": True,
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
        quality = snapshot.get("signal_quality_v4") or snapshot.get("signal_quality_v3") or snapshot.get("signal_quality_v2") or {}
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
            if any(str(candidate.get("state") or "").endswith(("_EARLY", "_CONFIRMED", "_CONTINUATION"))
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

    def persist_source_snapshot(self, *, symbol: str, exchange: str, environment: str,
                                source_type: str, provider: str, snapshot: Mapping[str, Any],
                                observed_at: str | None = None,
                                ttl_seconds: int | None = None) -> bool:
        """Persist one independently valid bounded public-source observation."""
        source_key = str(source_type).strip().upper()
        required = {"FUNDING": "funding_rate", "OPEN_INTEREST": "open_interest"}
        if source_key not in required:
            raise ValueError("unsupported market source type")
        if snapshot.get(required[source_key]) is None:
            raise ValueError(f"{source_key.lower()} snapshot has no reported value")
        payload = dict(snapshot)
        payload.pop("bids", None)
        payload.pop("asks", None)
        serialized = _canonical(payload)
        if len(serialized.encode("utf-8")) > 65_536:
            raise ValueError("market source snapshot exceeds the persistence size bound")
        observed = observed_at or datetime.now(timezone.utc).isoformat()
        parsed = datetime.fromisoformat(str(observed).replace("Z", "+00:00"))
        parsed = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        ttl = max(30, min(int(ttl_seconds or 300), 86_400))
        expires = (parsed + timedelta(seconds=ttl)).isoformat()
        identity = {"symbol": str(symbol).upper(), "exchange": str(exchange).lower(),
                    "environment": environment, "source_type": source_key,
                    "provider": provider, "observed_at": observed, "snapshot": payload}
        checksum = _checksum(identity)
        now = datetime.now(timezone.utc).isoformat()
        with connect() as conn:
            cur = conn.execute("""INSERT INTO market_source_snapshots(symbol,exchange,environment,
                source_type,provider,snapshot_version,snapshot_checksum,snapshot_json,
                observed_at,expires_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(snapshot_checksum) DO NOTHING""", (
                identity["symbol"], identity["exchange"], environment, source_key, provider,
                "market-source-history-v1", checksum, serialized, observed, expires, now))
        return cur.rowcount == 1

    def latest_source_snapshot(self, symbol: str, source_type: str,
                               as_of: str | None = None) -> dict[str, Any] | None:
        normalized = str(symbol).upper().replace("-", "")
        source_key = str(source_type).upper()
        cutoff = as_of or datetime.now(timezone.utc).isoformat()
        with connect() as conn:
            row = conn.execute("""SELECT * FROM market_source_snapshots
                WHERE REPLACE(UPPER(symbol),'-','')=? AND source_type=? AND observed_at<=?
                ORDER BY observed_at DESC,id DESC LIMIT 1""",
                (normalized, source_key, cutoff)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["snapshot"] = _loads(result.pop("snapshot_json", None), {})
        expires = _parse_datetime(result.get("expires_at"))
        result["stale"] = expires is None or expires < datetime.now(timezone.utc)
        return result

    def source_history(self, symbol: str, source_type: str, *, limit: int = 96,
                       as_of: str | None = None) -> dict[str, Any]:
        """Return chronological, bounded history and leakage-safe descriptive features."""
        normalized = str(symbol).upper().replace("-", "")
        source_key = str(source_type).upper()
        safe = max(2, min(int(limit), 500))
        cutoff = as_of or datetime.now(timezone.utc).isoformat()
        with connect() as conn:
            rows = [dict(row) for row in conn.execute("""SELECT * FROM (
                    SELECT * FROM market_source_snapshots
                    WHERE REPLACE(UPPER(symbol),'-','')=? AND source_type=? AND observed_at<=?
                    ORDER BY observed_at DESC,id DESC LIMIT ?
                ) bounded ORDER BY observed_at,id""",
                (normalized, source_key, cutoff, safe)).fetchall()]
        key = "funding_rate" if source_key == "FUNDING" else "open_interest"
        observations = []
        for row in rows:
            payload = _loads(row.get("snapshot_json"), {})
            value = _number(payload.get(key))
            if value is not None:
                observations.append({"observed_at": row["observed_at"], "value": value,
                                     "payload": payload})
        values = [item["value"] for item in observations]
        current = values[-1] if values else None
        delta = current - values[-2] if len(values) >= 2 else None
        change_pct = (delta / values[-2] * 100
                      if delta is not None and values[-2] != 0 else None)
        acceleration = None
        if len(values) >= 3:
            acceleration = (values[-1] - values[-2]) - (values[-2] - values[-3])
        percentile = None
        if source_key == "FUNDING" and len(values) >= 20 and current is not None:
            percentile = sum(value <= current for value in values) / len(values) * 100
        price_state = "INSUFFICIENT_HISTORY"
        if source_key == "OPEN_INTEREST" and len(observations) >= 2:
            current_price = _number(observations[-1]["payload"].get("reference_price"))
            prior_price = _number(observations[-2]["payload"].get("reference_price"))
            if current_price is not None and prior_price and delta is not None:
                price_state = f"PRICE_{'UP' if current_price >= prior_price else 'DOWN'}_OI_{'UP' if delta >= 0 else 'DOWN'}"
        return {"version": "market-source-history-v1", "source_type": source_key,
                "history_points": len(values), "status": ("AVAILABLE" if len(values) >= 3
                else "INSUFFICIENT_HISTORY" if values else "WAITING_FOR_FIRST_SAMPLE"),
                "current": current, "delta": delta, "change_pct": change_pct,
                "acceleration": acceleration, "percentile": percentile,
                "price_oi_state": price_state, "as_of": cutoff,
                "future_data_used": False,
                "observations": [{"observed_at": item["observed_at"], "value": item["value"]}
                                 for item in observations]}

    def microstructure_history(self, symbol: str, *, limit: int = 96,
                               as_of: str | None = None) -> dict[str, Any]:
        normalized = str(symbol).upper().replace("-", "")
        safe = max(2, min(int(limit), 500))
        cutoff = as_of or datetime.now(timezone.utc).isoformat()
        with connect() as conn:
            rows = [dict(row) for row in conn.execute("""SELECT * FROM (
                    SELECT sampled_at,aggregate_json FROM microstructure_aggregates
                    WHERE REPLACE(UPPER(symbol),'-','')=? AND sampled_at<=?
                    ORDER BY sampled_at DESC,id DESC LIMIT ?
                ) bounded ORDER BY sampled_at""", (normalized, cutoff, safe)).fetchall()]
        points = []
        for row in rows:
            aggregate = _loads(row.get("aggregate_json"), {})
            imbalance = _number(((aggregate.get("depth_bands") or {}).get("0.5") or {}).get("imbalance"))
            points.append({"sampled_at": row["sampled_at"], "spread_bps": _number(aggregate.get("spread_bps")),
                           "imbalance": imbalance, "book_pressure": imbalance,
                           "wall_count": len(aggregate.get("walls") or []),
                           "behavior_labels": list(aggregate.get("behavior_labels") or [])[:8]})
        def trend(key: str) -> str:
            values = [item[key] for item in points if item.get(key) is not None]
            if len(values) < 3:
                return "INSUFFICIENT_HISTORY"
            delta = sum(values[-2:]) / 2 - sum(values[:2]) / 2
            threshold = .05 if key != "spread_bps" else .5
            return "RISING" if delta > threshold else "FALLING" if delta < -threshold else "STABLE"
        return {"version": "microstructure-history-v1", "history_points": len(points),
                "status": "AVAILABLE" if len(points) >= 3 else "INSUFFICIENT_HISTORY",
                "imbalance_trend": trend("imbalance"), "spread_trend": trend("spread_bps"),
                "book_pressure_acceleration": (None if len(points) < 3 or any(
                    point.get("book_pressure") is None for point in points[-3:]) else
                    (points[-1]["book_pressure"] - points[-2]["book_pressure"])
                    - (points[-2]["book_pressure"] - points[-3]["book_pressure"])),
                "points": points, "as_of": cutoff, "future_data_used": False}

    def record_source_health(self, *, symbol: str, source_type: str, provider: str,
                             success: bool, error_code: str | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        normalized = str(symbol).upper().replace("-", "")
        state = "HEALTHY" if success else "FAILED"
        with connect() as conn:
            conn.execute("""INSERT INTO market_source_health(symbol,source_type,provider,state,
                last_attempt_at,last_success_at,last_error_code,consecutive_failures,
                samples_collected,samples_rejected,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(symbol,source_type,provider) DO UPDATE SET
                    state=excluded.state,last_attempt_at=excluded.last_attempt_at,
                    last_success_at=CASE WHEN excluded.state='HEALTHY' THEN excluded.last_success_at
                        ELSE market_source_health.last_success_at END,
                    last_error_code=CASE WHEN excluded.state='HEALTHY' THEN NULL ELSE excluded.last_error_code END,
                    consecutive_failures=CASE WHEN excluded.state='HEALTHY' THEN 0
                        ELSE market_source_health.consecutive_failures+1 END,
                    samples_collected=market_source_health.samples_collected+excluded.samples_collected,
                    samples_rejected=market_source_health.samples_rejected+excluded.samples_rejected,
                    updated_at=excluded.updated_at""", (
                    normalized, str(source_type).upper(), provider, state, now,
                    now if success else None, None if success else str(error_code or "SOURCE_ERROR")[:80],
                    0 if success else 1, int(success), int(not success), now))

    def update_worker_health(self, **changes: Any) -> dict[str, Any]:
        worker_name = str(changes.pop("worker_name", "microstructure_observer"))
        existing = self.worker_health(worker_name) or {}
        defaults = {"configured_value": None, "configured_enabled": 0, "effective_enabled": 0,
                    "state": "NOT_STARTED", "worker_started_at": None, "heartbeat_at": None,
                    "lease_state": "NOT_ACQUIRED", "lease_owner": None, "active_symbols": [],
                    "source_health": {}, "last_cycle_started_at": None, "last_cycle_completed_at": None,
                    "last_depth_success_at": None, "last_funding_success_at": None,
                    "last_oi_success_at": None, "last_persist_success_at": None,
                    "last_error_code": None, "consecutive_failures": 0,
                    "samples_collected": 0, "samples_rejected": 0,
                    "symbols_attempted": 0, "symbols_succeeded": 0,
                    "cycle_duration_ms": None, "provider": "BINGX_PUBLIC_FUTURES"}
        values = {**defaults, **existing, **changes}
        now = datetime.now(timezone.utc).isoformat()
        values["updated_at"] = now
        with connect() as conn:
            conn.execute("""INSERT INTO microstructure_worker_health(worker_name,configured_value,
                configured_enabled,effective_enabled,state,worker_started_at,heartbeat_at,
                lease_state,lease_owner,active_symbols_json,source_health_json,
                last_cycle_started_at,last_cycle_completed_at,last_depth_success_at,
                last_funding_success_at,last_oi_success_at,last_persist_success_at,last_error_code,
                consecutive_failures,samples_collected,samples_rejected,symbols_attempted,
                symbols_succeeded,cycle_duration_ms,provider,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(worker_name) DO UPDATE SET
                    configured_value=excluded.configured_value,configured_enabled=excluded.configured_enabled,
                    effective_enabled=excluded.effective_enabled,state=excluded.state,
                    worker_started_at=excluded.worker_started_at,heartbeat_at=excluded.heartbeat_at,
                    lease_state=excluded.lease_state,lease_owner=excluded.lease_owner,
                    active_symbols_json=excluded.active_symbols_json,source_health_json=excluded.source_health_json,
                    last_cycle_started_at=excluded.last_cycle_started_at,
                    last_cycle_completed_at=excluded.last_cycle_completed_at,
                    last_depth_success_at=excluded.last_depth_success_at,
                    last_funding_success_at=excluded.last_funding_success_at,
                    last_oi_success_at=excluded.last_oi_success_at,
                    last_persist_success_at=excluded.last_persist_success_at,
                    last_error_code=excluded.last_error_code,consecutive_failures=excluded.consecutive_failures,
                    samples_collected=excluded.samples_collected,samples_rejected=excluded.samples_rejected,
                    symbols_attempted=excluded.symbols_attempted,symbols_succeeded=excluded.symbols_succeeded,
                    cycle_duration_ms=excluded.cycle_duration_ms,provider=excluded.provider,
                    updated_at=excluded.updated_at""", (
                    worker_name, values["configured_value"], int(bool(values["configured_enabled"])),
                    int(bool(values["effective_enabled"])), values["state"], values["worker_started_at"],
                    values["heartbeat_at"], values["lease_state"], values["lease_owner"],
                    _canonical(values["active_symbols"]), _canonical(values["source_health"]),
                    values["last_cycle_started_at"], values["last_cycle_completed_at"],
                    values["last_depth_success_at"], values["last_funding_success_at"],
                    values["last_oi_success_at"], values["last_persist_success_at"],
                    values["last_error_code"], int(values["consecutive_failures"] or 0),
                    int(values["samples_collected"] or 0), int(values["samples_rejected"] or 0),
                    int(values["symbols_attempted"] or 0), int(values["symbols_succeeded"] or 0),
                    values["cycle_duration_ms"], values["provider"], now))
        return self.worker_health(worker_name) or {}

    def worker_health(self, worker_name: str = "microstructure_observer") -> dict[str, Any] | None:
        with connect() as conn:
            row = conn.execute("SELECT * FROM microstructure_worker_health WHERE worker_name=?",
                               (worker_name,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["active_symbols"] = _loads(result.pop("active_symbols_json", None), [])
        result["source_health"] = _loads(result.pop("source_health_json", None), {})
        return result

    def data_health(self, symbol: str | None = None,
                    owner_telegram_id: int | None = None) -> dict[str, Any]:
        """Separate current global sources from immutable decision-time availability."""
        market = self.latest_symbol(symbol, owner_telegram_id) if symbol else None
        micro = self.latest_microstructure(symbol) if symbol else None
        snapshot = (market or {}).get("full_snapshot") or {}
        funding = self.latest_source_snapshot(symbol, "FUNDING") if symbol else None
        open_interest = self.latest_source_snapshot(symbol, "OPEN_INTEREST") if symbol else None
        funding_history = self.source_history(symbol, "FUNDING") if symbol else {}
        oi_history = self.source_history(symbol, "OPEN_INTEREST") if symbol else {}
        worker = self.worker_health() or {}
        micro_enabled = os.getenv("MICROSTRUCTURE_COLLECTION_ENABLED", "false").strip().lower() in {
            "1", "true", "yes", "on"}
        collector_state = str(worker.get("state") or (
            "NOT_STARTED" if micro_enabled else "DISABLED_BY_CONFIGURATION"))

        def source_state(row: Mapping[str, Any] | None, history: Mapping[str, Any] | None = None) -> str:
            if row:
                if row.get("stale"):
                    return "STALE"
                if history and history.get("status") == "INSUFFICIENT_HISTORY":
                    return "INSUFFICIENT_HISTORY"
                return "HEALTHY"
            if not micro_enabled:
                return "DISABLED_BY_CONFIGURATION"
            if collector_state in {"FAILED", "DEGRADED", "STALE", "NOT_STARTED"}:
                return collector_state
            return "WAITING_FOR_FIRST_SAMPLE"

        try:
            from services.providers.okx import OKXProvider
            candle_provider = OKXProvider.health_snapshot()
            candle_state = str(candle_provider.get("status") or "UNKNOWN")
        except Exception:
            candle_state = "UNKNOWN"
        current_derivatives = (funding or {}).get("snapshot") or {}
        current_oi = (open_interest or {}).get("snapshot") or {}
        funding_context = {**current_derivatives,
                           "history_status": funding_history.get("status"),
                           "history_points": funding_history.get("history_points"),
                           "funding_delta": funding_history.get("delta"),
                           "funding_acceleration": funding_history.get("acceleration"),
                           "funding_percentile": funding_history.get("percentile")} if funding else {}
        oi_context = {**current_oi,
                      "history_status": oi_history.get("status"),
                      "history_points": oi_history.get("history_points"),
                      "open_interest_delta": oi_history.get("delta"),
                      "open_interest_change_pct": oi_history.get("change_pct"),
                      "oi_acceleration": oi_history.get("acceleration"),
                      "price_oi_state": oi_history.get("price_oi_state")} if open_interest else {}
        global_health = {
            "candles": candle_state,
            "benchmark": "HEALTHY" if (snapshot.get("relative_strength") or {}).get("status") == "AVAILABLE" else "SOURCE_AVAILABLE_DECISION_FRAME_REQUIRED",
            "funding": source_state(funding, funding_history),
            "open_interest": source_state(open_interest, oi_history),
            "microstructure": source_state(micro),
            "collector": collector_state,
        }
        decision_health = {
            "candles": "AVAILABLE" if market else "MISSING",
            "benchmark": "AVAILABLE" if (snapshot.get("relative_strength") or {}).get("status") == "AVAILABLE" else "MISSING_SYNCHRONIZED_FRAME",
            "funding": "CAPTURED" if (snapshot.get("funding_open_interest") or {}).get("funding_rate") is not None else "NOT_CAPTURED",
            "open_interest": "CAPTURED" if (snapshot.get("funding_open_interest") or {}).get("open_interest") is not None else "NOT_CAPTURED",
            "microstructure": "CAPTURED" if (snapshot.get("microstructure") or {}).get("status") == "AVAILABLE" else "NOT_CAPTURED",
            "liquidity_map": "AVAILABLE" if snapshot.get("liquidity_map") else "MISSING",
            "ordered_path": "INSUFFICIENT_HISTORY",
            "execution_costs": "AVAILABLE" if (snapshot.get("research_policies") or {}).get(
                "estimated_roundtrip_cost_pct") is not None else "MISSING",
        }
        return {
            "version": "data-availability-v3", "symbol": symbol,
            "global_source_health": global_health,
            "decision_snapshot_availability": decision_health,
            "candles": decision_health["candles"], "benchmark": decision_health["benchmark"],
            "funding": global_health["funding"], "open_interest": global_health["open_interest"],
            "microstructure": global_health["microstructure"],
            "liquidity_map": decision_health["liquidity_map"],
            "ordered_path": decision_health["ordered_path"],
            "execution_costs": decision_health["execution_costs"],
            "remediation": {
                "microstructure": ("Set MICROSTRUCTURE_COLLECTION_ENABLED=true and redeploy the Render service."
                                   if not micro_enabled else
                                   f"Collector state is {collector_state}; inspect operator worker telemetry and its last error code."),
                "benchmark": "Supply a synchronized BTC decision-time frame; never backfill with future candles.",
                "ordered_path": "Accumulate trustworthy ordered-path observations.",
            },
            "funding_context": funding_context,
            "open_interest_context": oi_context,
            "microstructure_history": self.microstructure_history(symbol) if symbol else {},
            "collector": worker,
            "source_diagnostics": ({source: self.pipeline_diagnostics(symbol, source)
                                    for source in ("DEPTH", "FUNDING", "OPEN_INTEREST")}
                                   if symbol else {}),
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

    def quality_calibration_cohorts(self, owner_telegram_id: int | None = None) -> dict[str, Any]:
        """Research-only 20-point Quality buckets with honest missing outcome fields."""
        with connect() as conn:
            rows = [dict(row) for row in conn.execute("""SELECT m.overall_quality,
                m.research_policies_json,o.outcome_json FROM market_intelligence_snapshots m
                JOIN research_signal_snapshots r ON r.snapshot_id=m.research_snapshot_id
                LEFT JOIN research_outcomes o ON o.snapshot_id=m.research_snapshot_id AND o.id=(
                    SELECT MAX(o2.id) FROM research_outcomes o2 WHERE o2.snapshot_id=m.research_snapshot_id)
                WHERE r.capture_quality='DECISION_TIME' AND
                  (? IS NULL OR m.owner_telegram_id IS NULL OR m.owner_telegram_id=0
                   OR m.owner_telegram_id=?) ORDER BY m.decision_at,m.id""",
                (owner_telegram_id, owner_telegram_id)).fetchall()]
        bands = [(0, 20), (20, 40), (40, 60), (60, 80), (80, 101)]
        samples: dict[str, list[dict[str, float | None]]] = {
            f"{low}-{100 if high == 101 else high}": [] for low, high in bands}
        for row in rows:
            outcome = _loads(row.get("outcome_json"), {})
            pure = outcome.get("pure_market") if isinstance(outcome.get("pure_market"), dict) else {}
            if not pure.get("eligible") or pure.get("signal_r") is None:
                continue
            quality = float(row.get("overall_quality") or 0)
            low, high = next((low, high) for low, high in bands if low <= quality < high)
            costs = _loads(row.get("research_policies_json"), {}).get("estimated_cost_r")
            samples[f"{low}-{100 if high == 101 else high}"].append({
                "r": float(pure["signal_r"]),
                "mfe": _number(pure.get("mfe_r") if pure.get("mfe_r") is not None else pure.get("mfe")),
                "mae": _number(pure.get("mae_r") if pure.get("mae_r") is not None else pure.get("mae")),
                "cost": _number(costs),
            })
        cohorts = []
        for label, values in samples.items():
            rs = [float(item["r"] or 0) for item in values]
            wins, losses = [v for v in rs if v > 0], [v for v in rs if v < 0]
            gross_win, gross_loss = sum(wins), abs(sum(losses))
            cost_adjusted = [value - float(item.get("cost") or 0)
                             for value, item in zip(rs, values)]
            cohorts.append({
                "bucket": label, "n": len(rs),
                "status": "SUFFICIENT" if len(rs) >= 20 else "INSUFFICIENT",
                "win_rate_pct": round(len(wins) / len(rs) * 100, 3) if rs else None,
                "expectancy_r": round(sum(rs) / len(rs), 5) if rs else None,
                "mfe_r": (round(sum(v for v in (_number(i.get("mfe")) for i in values) if v is not None)
                                / sum(_number(i.get("mfe")) is not None for i in values), 5)
                          if any(_number(i.get("mfe")) is not None for i in values) else None),
                "mae_r": (round(sum(v for v in (_number(i.get("mae")) for i in values) if v is not None)
                                / sum(_number(i.get("mae")) is not None for i in values), 5)
                          if any(_number(i.get("mae")) is not None for i in values) else None),
                "profit_factor": round(gross_win / gross_loss, 5) if gross_loss else None,
                "cost_adjusted_expectancy_r": (round(sum(cost_adjusted) / len(cost_adjusted), 5)
                                                if cost_adjusted else None),
            })
        return {"version": "quality-calibration-v1", "cohorts": cohorts,
                "resolved_samples": sum(len(items) for items in samples.values()),
                "score_is_probability": False, "automatic_threshold_change": False}

    def readiness_timing_cohorts(self, owner_telegram_id: int | None = None) -> dict[str, Any]:
        states = ("READY", "WAIT_STRUCTURE", "WAIT_CONFIRMATION", "WAIT_PULLBACK", "CHASING", "INVALID")
        grouped: dict[str, list[dict[str, Any]]] = {state: [] for state in states}
        with connect() as conn:
            rows = [dict(row) for row in conn.execute("""SELECT m.full_snapshot_json,o.outcome_json
                FROM market_intelligence_snapshots m JOIN research_signal_snapshots r
                  ON r.snapshot_id=m.research_snapshot_id
                LEFT JOIN research_outcomes o ON o.snapshot_id=m.research_snapshot_id AND o.id=(
                  SELECT MAX(o2.id) FROM research_outcomes o2 WHERE o2.snapshot_id=m.research_snapshot_id)
                WHERE r.capture_quality='DECISION_TIME' AND
                  (? IS NULL OR m.owner_telegram_id IS NULL OR m.owner_telegram_id=0
                   OR m.owner_telegram_id=?)""", (owner_telegram_id, owner_telegram_id)).fetchall()]
        for row in rows:
            snapshot = _loads(row.get("full_snapshot_json"), {})
            readiness = snapshot.get("entry_readiness") or {}
            state = str(readiness.get("state") or "")
            if state not in grouped:
                continue
            outcome = _loads(row.get("outcome_json"), {})
            pure = outcome.get("pure_market") if isinstance(outcome.get("pure_market"), dict) else {}
            if pure.get("eligible"):
                grouped[state].append(pure)
        cohorts = []
        for state, items in grouped.items():
            values = [_number(item.get("signal_r")) for item in items]
            values = [value for value in values if value is not None]
            def average(*keys: str) -> float | None:
                found = [_number(next((item.get(key) for key in keys if item.get(key) is not None), None))
                         for item in items]
                found = [value for value in found if value is not None]
                return round(sum(found) / len(found), 5) if found else None
            cohorts.append({"state": state, "n": len(values),
                            "status": "SUFFICIENT" if len(values) >= 20 else "INSUFFICIENT",
                            "subsequent_mfe": average("mfe_r", "mfe"),
                            "subsequent_mae": average("mae_r", "mae"),
                            "time_to_entry_seconds": average("time_to_entry_seconds"),
                            "time_to_invalidation_seconds": average("time_to_invalidation_seconds"),
                            "missed_winners": sum(value > 0 for value in values) if state != "READY" else 0,
                            "avoided_losers": sum(value < 0 for value in values) if state != "READY" else 0})
        return {"version": "entry-readiness-calibration-v1", "cohorts": cohorts,
                "automatic_policy_change": False, "future_data_in_decision_features": False}

    def strategy_separation_diagnostics(self, telegram_id: int, limit: int = 1000) -> dict[str, Any]:
        safe = max(10, min(int(limit), 5000))
        with connect() as conn:
            rows = [dict(row) for row in conn.execute(f"""SELECT strategy_suitability_json
                FROM market_intelligence_snapshots WHERE owner_telegram_id=? OR owner_telegram_id IS NULL
                ORDER BY id DESC LIMIT {safe}""", (telegram_id,)).fetchall()]
        vectors = [_loads(row.get("strategy_suitability_json"), {}) for row in rows]
        strategies = sorted({str(key) for vector in vectors for key in vector})
        pairs = []
        def correlation(left: list[float], right: list[float]) -> float | None:
            if len(left) < 3 or len(left) != len(right):
                return None
            lm, rm = sum(left) / len(left), sum(right) / len(right)
            numerator = sum((a - lm) * (b - rm) for a, b in zip(left, right))
            denominator = math.sqrt(sum((a - lm) ** 2 for a in left) * sum((b - rm) ** 2 for b in right))
            return round(numerator / denominator, 5) if denominator else None
        for index, left_name in enumerate(strategies):
            for right_name in strategies[index + 1:]:
                observed = [(float(v[left_name]), float(v[right_name])) for v in vectors
                            if left_name in v and right_name in v]
                left_scores, right_scores = [x[0] for x in observed], [x[1] for x in observed]
                left_set = {i for i, value in enumerate(left_scores) if value >= 60}
                right_set = {i for i, value in enumerate(right_scores) if value >= 60}
                union, overlap = left_set | right_set, left_set & right_set
                identical = sum((a >= 60) == (b >= 60) for a, b in observed)
                pairs.append({"left": left_name, "right": right_name, "n": len(observed),
                              "identical_decision_rate": round(identical / len(observed), 5) if observed else None,
                              "jaccard_overlap": round(len(overlap) / len(union), 5) if union else None,
                              "accepted_set_overlap": len(overlap),
                              "score_correlation": correlation(left_scores, right_scores),
                              "outcome_correlation": None,
                              "outcome_correlation_status": "DISTINCT_STRATEGY_OUTCOMES_NOT_AVAILABLE"})
        return {"version": "strategy-separation-v1", "snapshots": len(vectors), "pairs": pairs,
                "forced_diversity": False, "research_only": True, "execution_authority": False}

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
