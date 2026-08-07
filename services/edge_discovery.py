from __future__ import annotations

import hashlib
import itertools
import json
import math
import os
import random
import statistics
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable

from database.database import connect
from version import APP_VERSION


FEATURE_VERSION = "research-features-v2"
ALGORITHM_VERSION = "edge-discovery-v1"
MODEL_VERSION = "regularized-logit-v1"
SELECTOR_VERSION = "regime-selector-v1"
RANK_VERSION = "research-rank-v2"
TARGET_DEFINITION = "PURE_MARKET_SIGNAL_R_POSITIVE_V1"

BINARY_FEATURES = (
    "bos", "choch", "htf_aligned", "liquidity_sweep", "equal_high_low",
    "fvg", "order_block", "breaker", "mitigation", "displacement",
    "compression", "expansion", "weekend", "low_liquidity_hours",
)
NUMERIC_MODEL_FEATURES = (
    "confidence", "planned_rr", "structural_strength", "liquidity_proximity",
    "rsi", "macd", "momentum_score", "ema_slope", "atr_pct",
)
FORBIDDEN_DECISION_KEYS = {
    "result", "realized_r", "closed_at", "outcome", "execution_outcome",
    "max_profit_pct", "max_drawdown_pct", "mfe", "mae", "future_price",
}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False, default=str)


def _checksum(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


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


def _utc(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _flatten(value: Any, prefix: str = "", depth: int = 0) -> dict[str, Any]:
    if depth > 4 or not isinstance(value, dict):
        return {prefix: value} if prefix else {}
    result: dict[str, Any] = {}
    for raw_key in sorted(value):
        key = str(raw_key).strip().lower().replace("-", "_").replace(" ", "_")
        path = f"{prefix}.{key}" if prefix else key
        child = value[raw_key]
        if isinstance(child, dict):
            result.update(_flatten(child, path, depth + 1))
        else:
            result[path] = child
    return result


def _lookup(flat: dict[str, Any], *aliases: str) -> Any:
    for alias in aliases:
        key = alias.lower()
        if key in flat:
            return flat[key]
        suffix = f".{key}"
        for path in sorted(flat):
            if path.endswith(suffix):
                return flat[path]
        for leaf in ("present", "enabled", "confirmed", "detected", "value", "state"):
            nested = f"{key}.{leaf}"
            if nested in flat:
                return flat[nested]
            nested_suffix = f".{nested}"
            for path in sorted(flat):
                if path.endswith(nested_suffix):
                    return flat[path]
    return None


def _boolean(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().upper()
    if text in {"TRUE", "YES", "Y", "1", "PRESENT", "CONFIRMED", "ALIGNED", "BULLISH", "BEARISH"}:
        return True
    if text in {"FALSE", "NO", "N", "0", "ABSENT", "NONE", "NOT_PRESENT", "UNALIGNED"}:
        return False
    return True if text else None


def _category(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("code") or value.get("state") or value.get("label")
    text = "_".join(str(value or "").strip().upper().split())
    return text or None


def _variance(values: list[float]) -> float:
    return statistics.variance(values) if len(values) > 1 else 0.0


class ResearchFeatureNormalizer:
    """Whitelisted, versioned normalization of immutable decision snapshots only."""

    aliases = {
        "bos": ("bos", "break_of_structure"),
        "choch": ("choch", "change_of_character"),
        "htf_aligned": ("htf_alignment", "higher_timeframe_alignment", "multi_timeframe_alignment"),
        "liquidity_sweep": ("sweep", "liquidity_sweep", "stop_hunt"),
        "equal_high_low": ("eql_eqh", "equal_highs_lows", "equal_highs", "equal_lows"),
        "fvg": ("fvg", "fair_value_gap", "fvg_present"),
        "order_block": ("order_block", "ob", "order_block_present"),
        "breaker": ("breaker", "breaker_block"),
        "mitigation": ("mitigation", "mitigation_block"),
        "displacement": ("displacement", "impulse"),
        "compression": ("compression", "squeeze"),
        "expansion": ("expansion", "volatility_expansion"),
    }

    @classmethod
    def normalize(cls, snapshot_row: dict[str, Any]) -> dict[str, Any]:
        snapshot = _loads(snapshot_row.get("snapshot_json"), {})
        features = snapshot.get("features") if isinstance(snapshot.get("features"), dict) else {}
        flat = _flatten(features)
        decision = _utc(snapshot.get("decision_at") or snapshot_row.get("decision_at"))
        hour = decision.hour if decision else None
        session_tags: list[str] = []
        if hour is not None:
            if 0 <= hour < 8:
                session_tags.append("ASIA")
            if 7 <= hour < 10:
                session_tags.append("ASIA_LONDON_OVERLAP")
            if 8 <= hour < 14:
                session_tags.append("LONDON")
            if 13 <= hour < 17:
                session_tags.append("LONDON_NEW_YORK_OVERLAP")
            if 13 <= hour < 21:
                session_tags.append("NEW_YORK")
            if hour >= 21:
                session_tags.append("LOW_LIQUIDITY_HOURS")
        entry = _number(snapshot.get("entry"))
        stop = _number(snapshot.get("stop"))
        risk = abs(entry - stop) if entry is not None and stop is not None else None
        vector: dict[str, Any] = {}
        for name, aliases in cls.aliases.items():
            vector[name] = _boolean(_lookup(flat, *aliases))
        vector["weekend"] = decision.weekday() >= 5 if decision else None
        vector["low_liquidity_hours"] = "LOW_LIQUIDITY_HOURS" in session_tags if decision else None
        vector.update({
            "trend_direction": _category(_lookup(flat, "trend_direction", "trend", "htf_trend")),
            "liquidity_direction": _category(_lookup(flat, "liquidity_direction", "sweep_direction")),
            "ema_relationship": _category(_lookup(flat, "ema_relationship", "ema_state")),
            "volatility_state": _category(_lookup(flat, "volatility_state", "volatility")),
            "primary_regime": _category(snapshot.get("primary_regime")) or "UNKNOWN",
            "timeframe": str(snapshot.get("timeframe") or "UNKNOWN").lower(),
            "session": session_tags[0] if session_tags else _category(snapshot.get("session")) or "UNKNOWN",
            "session_tags": session_tags or ["UNKNOWN"],
            "symbol": str(snapshot.get("symbol") or "UNKNOWN").upper(),
            "direction": _category(snapshot.get("side")) or "UNKNOWN",
            "setup_family": _category(snapshot.get("setup_family")) or "UNKNOWN",
            "weekday_utc": decision.weekday() if decision else None,
            "hour_utc": hour,
            "structural_strength": _number(_lookup(flat, "structural_strength", "structure_score")),
            "liquidity_proximity": _number(_lookup(flat, "liquidity_proximity", "distance_to_liquidity")),
            "rsi": _number(_lookup(flat, "rsi", "rsi_value")),
            "macd": _number(_lookup(flat, "macd", "macd_histogram", "macd_value")),
            "momentum_score": _number(_lookup(flat, "momentum_score", "momentum")),
            "ema50": _number(_lookup(flat, "ema50", "ema_50")),
            "ema200": _number(_lookup(flat, "ema200", "ema_200")),
            "ema_slope": _number(_lookup(flat, "ema_slope", "trend_slope")),
            "atr": _number(_lookup(flat, "atr", "atr_value")),
            "atr_pct": _number(_lookup(flat, "atr_pct", "atr_percent")),
            "planned_rr": _number(snapshot.get("rr")),
            "confidence": _number(snapshot.get("confidence")),
            "bull_score": _number(snapshot.get("bull_score")),
            "bear_score": _number(snapshot.get("bear_score")),
            "entry": entry,
            "stop": stop,
            "stop_distance_pct": (risk / abs(entry) * 100 if risk is not None and entry else None),
        })
        targets = list(snapshot.get("take_profits") or [])[:3]
        targets += [None] * (3 - len(targets))
        for index, target in enumerate(targets, 1):
            target_value = _number(target)
            vector[f"tp{index}_r"] = (
                abs(target_value - entry) / risk
                if target_value is not None and entry is not None and risk not in {None, 0}
                else None
            )
        missing = sorted(key for key, value in vector.items() if value is None)
        snapshot_keys = set(_flatten(snapshot))
        contaminated = sorted(
            key for key in snapshot_keys
            if key.rsplit(".", 1)[-1] in FORBIDDEN_DECISION_KEYS
        )
        capture_quality = str(snapshot_row.get("capture_quality") or "UNKNOWN").upper()
        essentials_missing = any(vector.get(key) in {None, "", "UNKNOWN"}
                                 for key in ("symbol", "timeframe", "direction", "entry", "stop"))
        if contaminated:
            data_quality = "CONTAMINATED"
        elif capture_quality == "DECISION_TIME" and not essentials_missing:
            data_quality = "TRUSTWORTHY_DECISION_TIME"
        elif capture_quality in {"RECONSTRUCTED", "HISTORICAL_RECONSTRUCTION"}:
            data_quality = "RECONSTRUCTED"
        elif capture_quality != "DECISION_TIME":
            data_quality = "LATE_BACKFILL"
        else:
            data_quality = "INCOMPLETE"
        return {
            "feature_version": FEATURE_VERSION,
            "data_quality": data_quality,
            "missing_features": missing,
            "contaminated_keys": contaminated,
            "vector": vector,
        }


class StatisticalResearch:
    @staticmethod
    def sample_tier(n: int) -> str:
        minimum = max(3, int(os.getenv("EDGE_MIN_SAMPLES", "20")))
        moderate = max(minimum + 1, int(os.getenv("EDGE_MODERATE_SAMPLES", "50")))
        high = max(moderate + 1, int(os.getenv("EDGE_HIGH_SAMPLES", "100")))
        if n < minimum:
            return "VERY_LOW"
        if n < moderate:
            return "LOW"
        if n < high:
            return "MODERATE"
        return "HIGH"

    @staticmethod
    def bootstrap_interval(values: list[float], *, seed_key: str = "", confidence: float = .95) -> list[float] | None:
        if len(values) < 3:
            return None
        count = max(100, min(int(os.getenv("EDGE_BOOTSTRAP_SAMPLES", "500")), 5000))
        seed = int(hashlib.sha256((seed_key + _canonical(values)).encode()).hexdigest()[:16], 16)
        rng = random.Random(seed)
        means = sorted(sum(values[rng.randrange(len(values))] for _ in values) / len(values)
                       for _ in range(count))
        alpha = (1 - confidence) / 2
        low = means[max(0, min(count - 1, int(alpha * count)))]
        high = means[max(0, min(count - 1, int((1 - alpha) * count) - 1))]
        return [round(low, 4), round(high, 4)]

    @classmethod
    def metrics(cls, observations: Iterable[dict[str, Any]], *, seed_key: str = "") -> dict[str, Any]:
        rows = list(observations)
        values = [float(row["signal_r"]) for row in rows]
        n = len(values)
        wins = [value for value in values if value > 1e-12]
        losses = [value for value in values if value < -1e-12]
        breakeven = n - len(wins) - len(losses)
        expectancy = statistics.fmean(values) if values else None
        deviation = statistics.stdev(values) if n > 1 else None
        downside = math.sqrt(statistics.fmean(min(0.0, value) ** 2 for value in values)) if values else None
        bootstrap_max_n = max(50, min(int(os.getenv("EDGE_BOOTSTRAP_MAX_N", "500")), 5000))
        if n > bootstrap_max_n and deviation is not None:
            margin = 1.96 * deviation / math.sqrt(n)
            interval = [round((expectancy or 0.0) - margin, 4),
                        round((expectancy or 0.0) + margin, 4)]
            interval_method = "NORMAL_APPROXIMATION_LARGE_N"
        else:
            interval = cls.bootstrap_interval(values, seed_key=seed_key)
            interval_method = "DETERMINISTIC_BOOTSTRAP" if interval else None
        tier = cls.sample_tier(n)
        minimum = max(3, int(os.getenv("EDGE_MIN_SAMPLES", "20")))
        moderate = max(minimum + 1, int(os.getenv("EDGE_MODERATE_SAMPLES", "50")))
        evidence = "INSUFFICIENT" if n < minimum else "EXPLORATORY"
        if n >= moderate and interval and interval[0] > 0:
            evidence = "PROMISING"
        equity = peak = drawdown = 0.0
        for value in values:
            equity += value
            peak = max(peak, equity)
            drawdown = min(drawdown, equity - peak)
        mfe = [_number(row.get("mfe_r")) for row in rows]
        mae = [_number(row.get("mae_r")) for row in rows]
        return {
            "sample_size": n, "wins": len(wins), "losses": len(losses), "breakeven": breakeven,
            "win_rate": len(wins) / n if n else None,
            "expectancy_r": expectancy, "median_r": statistics.median(values) if values else None,
            "average_win_r": statistics.fmean(wins) if wins else None,
            "average_loss_r": statistics.fmean(losses) if losses else None,
            "payoff_ratio": (statistics.fmean(wins) / abs(statistics.fmean(losses))
                             if wins and losses else None),
            "profit_factor": sum(wins) / abs(sum(losses)) if losses else None,
            "average_mfe_r": statistics.fmean(value for value in mfe if value is not None)
            if any(value is not None for value in mfe) else None,
            "average_mae_r": statistics.fmean(value for value in mae if value is not None)
            if any(value is not None for value in mae) else None,
            "drawdown_proxy_r": drawdown if values else None,
            "standard_deviation_r": deviation,
            "downside_deviation_r": downside,
            "sharpe_like": expectancy / deviation if n >= 30 and deviation else None,
            "sortino_like": expectancy / downside if n >= 30 and downside else None,
            "expectancy_interval_95": interval,
            "expectancy_interval_method": interval_method,
            "sample_tier": tier, "evidence_state": evidence,
        }

    @staticmethod
    def delta_interval(left: list[float], right: list[float]) -> list[float] | None:
        if len(left) < 2 or len(right) < 2:
            return None
        delta = statistics.fmean(left) - statistics.fmean(right)
        se = math.sqrt(_variance(left) / len(left) + _variance(right) / len(right))
        return [round(delta - 1.96 * se, 4), round(delta + 1.96 * se, 4)]


class EdgeDiscoveryEngine:
    """Reproducible, bounded research projections with no execution dependencies."""

    def normalize_pending(self, limit: int = 200) -> int:
        safe = max(1, min(int(limit), 1000))
        with connect() as conn:
            rows = [dict(row) for row in conn.execute(f"""SELECT r.* FROM research_signal_snapshots r
                WHERE NOT EXISTS(SELECT 1 FROM research_feature_vectors f
                    WHERE f.snapshot_id=r.snapshot_id AND f.feature_version=?)
                ORDER BY r.id LIMIT {safe}""", (FEATURE_VERSION,)).fetchall()]
        created = 0
        now = datetime.now(timezone.utc).isoformat()
        for row in rows:
            normalized = ResearchFeatureNormalizer.normalize(row)
            payload = normalized["vector"]
            with connect() as conn:
                cur = conn.execute("""INSERT INTO research_feature_vectors(snapshot_id,signal_id,
                    feature_version,vector_checksum,data_quality,missing_features_json,vector_json,normalized_at)
                    VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(snapshot_id,feature_version) DO NOTHING""", (
                    row["snapshot_id"], row["signal_id"], FEATURE_VERSION, _checksum(payload),
                    normalized["data_quality"], _canonical(normalized["missing_features"]),
                    _canonical(payload), now))
            created += int(cur.rowcount == 1)
        return created

    @staticmethod
    def _outcome_layers(outcome: dict[str, Any]) -> tuple[bool, float | None, float | None, float | None]:
        pure = outcome.get("pure_market") if isinstance(outcome.get("pure_market"), dict) else {}
        if pure:
            eligible = bool(pure.get("eligible"))
            signal_r = _number(pure.get("signal_r"))
            mfe_r = _number(pure.get("max_favorable_r"))
            mae_r = _number(pure.get("max_adverse_r"))
        else:
            eligible = not bool(outcome.get("manual_intervention"))
            signal_r = _number(outcome.get("signal_r"))
            mfe_r = None
            mae_r = None
        return eligible, signal_r, mfe_r, mae_r

    @staticmethod
    def observations(telegram_id: int | None = None, *, cutoff: str | None = None,
                     after: str | None = None, limit: int | None = None,
                     include_all: bool = False) -> list[dict[str, Any]]:
        clauses = ["f.feature_version=?", "f.data_quality='TRUSTWORTHY_DECISION_TIME'",
                   "r.capture_quality='DECISION_TIME'"]
        params: list[Any] = [FEATURE_VERSION]
        if telegram_id is not None:
            clauses.append("(r.owner_telegram_id IS NULL OR r.owner_telegram_id=0 OR r.owner_telegram_id=?)")
            params.append(telegram_id)
        elif not include_all:
            clauses.append("(r.owner_telegram_id IS NULL OR r.owner_telegram_id=0)")
        if cutoff:
            clauses.append("r.decision_at<=?")
            params.append(cutoff)
        if after:
            clauses.append("r.decision_at>?")
            params.append(after)
        where = " AND ".join(clauses)
        requested_limit = limit if limit is not None else int(os.getenv("EDGE_HISTORY_LIMIT", "5000"))
        safe_limit = max(1, min(int(requested_limit), 50000))
        order_limit = f"ORDER BY r.decision_at DESC,r.id DESC LIMIT {safe_limit}"
        with connect() as conn:
            rows = [dict(row) for row in conn.execute(f"""SELECT r.*,f.vector_json,f.data_quality,
                o.outcome_json FROM research_signal_snapshots r
                JOIN research_feature_vectors f ON f.snapshot_id=r.snapshot_id
                JOIN research_outcomes o ON o.snapshot_id=r.snapshot_id AND o.id=(
                    SELECT MAX(o2.id) FROM research_outcomes o2 WHERE o2.snapshot_id=r.snapshot_id)
                WHERE {where} {order_limit}""", tuple(params)).fetchall()]
        rows.reverse()
        result = []
        for row in rows:
            outcome = _loads(row.get("outcome_json"), {})
            eligible, signal_r, mfe_r, mae_r = EdgeDiscoveryEngine._outcome_layers(outcome)
            if not eligible or signal_r is None:
                continue
            row["vector"] = _loads(row.get("vector_json"), {})
            row["outcome"] = outcome
            row["signal_r"], row["mfe_r"], row["mae_r"] = signal_r, mfe_r, mae_r
            result.append(row)
        return result

    @staticmethod
    def data_quality_report(telegram_id: int | None = None) -> dict[str, Any]:
        params: list[Any] = [FEATURE_VERSION]
        owner = ""
        if telegram_id is not None:
            owner = "AND (r.owner_telegram_id IS NULL OR r.owner_telegram_id=0 OR r.owner_telegram_id=?)"
            params.append(telegram_id)
        with connect() as conn:
            rows = conn.execute(f"""SELECT f.data_quality,COUNT(*) sample_count
                FROM research_feature_vectors f JOIN research_signal_snapshots r ON r.snapshot_id=f.snapshot_id
                WHERE f.feature_version=? {owner} GROUP BY f.data_quality""", tuple(params)).fetchall()
        counts = {str(row["data_quality"]): int(row["sample_count"]) for row in rows}
        return {"feature_version": FEATURE_VERSION, "counts": counts,
                "eligible_for_evidence": counts.get("TRUSTWORTHY_DECISION_TIME", 0),
                "excluded": sum(value for key, value in counts.items()
                                if key != "TRUSTWORTHY_DECISION_TIME")}

    @staticmethod
    def _feature_matches(row: dict[str, Any], definition: dict[str, Any]) -> bool:
        vector = row["vector"]
        if "feature" in definition:
            return vector.get(str(definition["feature"])) == definition.get("equals", True)
        if "all_true" in definition:
            return all(vector.get(str(name)) is True for name in definition["all_true"])
        if "equals" in definition and isinstance(definition["equals"], dict):
            return all(vector.get(str(key)) == value for key, value in definition["equals"].items())
        return False

    def feature_contributions(self, telegram_id: int | None = None,
                              *, rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        rows = self.observations(telegram_id) if rows is None else rows
        minimum = max(3, int(os.getenv("EDGE_MIN_SAMPLES", "20")))
        results = []
        for feature in BINARY_FEATURES:
            present = [row for row in rows if row["vector"].get(feature) is True]
            absent = [row for row in rows if row["vector"].get(feature) is False]
            strata: dict[tuple[str, str, str], tuple[list[dict[str, Any]], list[dict[str, Any]]]] = {}
            keys = sorted({(str(row["vector"].get("timeframe")),
                            str(row["vector"].get("primary_regime")),
                            str(row["vector"].get("direction"))) for row in present + absent})
            for key in keys:
                left = [row for row in present if (
                    str(row["vector"].get("timeframe")), str(row["vector"].get("primary_regime")),
                    str(row["vector"].get("direction"))) == key]
                right = [row for row in absent if (
                    str(row["vector"].get("timeframe")), str(row["vector"].get("primary_regime")),
                    str(row["vector"].get("direction"))) == key]
                if len(left) >= 2 and len(right) >= 2:
                    strata[key] = (left, right)
            controlled_present = [row for pair in strata.values() for row in pair[0]]
            controlled_absent = [row for pair in strata.values() for row in pair[1]]
            use_controlled = len(controlled_present) >= minimum and len(controlled_absent) >= minimum
            left = controlled_present if use_controlled else present
            right = controlled_absent if use_controlled else absent
            left_metrics = StatisticalResearch.metrics(left, seed_key=f"{feature}:present")
            right_metrics = StatisticalResearch.metrics(right, seed_key=f"{feature}:absent")
            left_r = [float(row["signal_r"]) for row in left]
            right_r = [float(row["signal_r"]) for row in right]
            delta = (statistics.fmean(left_r) - statistics.fmean(right_r)) if left_r and right_r else None
            win_delta = ((sum(value > 0 for value in left_r) / len(left_r)) -
                         (sum(value > 0 for value in right_r) / len(right_r))) if left_r and right_r else None
            mfe_left = [float(row["mfe_r"]) for row in left if row.get("mfe_r") is not None]
            mfe_right = [float(row["mfe_r"]) for row in right if row.get("mfe_r") is not None]
            mae_left = [float(row["mae_r"]) for row in left if row.get("mae_r") is not None]
            mae_right = [float(row["mae_r"]) for row in right if row.get("mae_r") is not None]
            results.append({
                "feature": feature, "present": left_metrics, "absent": right_metrics,
                "expectancy_delta_r": delta, "win_rate_delta": win_delta,
                "mfe_delta_r": statistics.fmean(mfe_left) - statistics.fmean(mfe_right)
                if mfe_left and mfe_right else None,
                "mae_delta_r": statistics.fmean(mae_left) - statistics.fmean(mae_right)
                if mae_left and mae_right else None,
                "expectancy_delta_interval_95": StatisticalResearch.delta_interval(left_r, right_r),
                "control": "TIMEFRAME_REGIME_DIRECTION_STRATIFIED" if use_controlled
                else "UNADJUSTED_INSUFFICIENT_STRATA",
                "controlled_strata": len(strata),
                "evidence_state": "INSUFFICIENT" if min(len(left), len(right)) < minimum else "EXPLORATORY",
                "causal_claim": False,
            })
        return {"feature_version": FEATURE_VERSION, "algorithm_version": ALGORITHM_VERSION,
                "observations": len(rows), "features": results,
                "warning": "Descriptive conditional association; no causal claim."}

    def cohort_edges(self, telegram_id: int | None = None,
                     *, rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        rows = self.observations(telegram_id) if rows is None else rows
        groups: dict[str, dict[str, list[dict[str, Any]]]] = {
            name: defaultdict(list) for name in (
                "timeframe", "regime", "session", "weekday_utc", "hour_utc", "symbol",
                "direction", "setup_family", "confidence_band", "planned_rr_band")
        }
        for row in rows:
            vector = row["vector"]
            confidence = _number(vector.get("confidence"))
            confidence_band = "MISSING" if confidence is None else (
                "90+" if confidence >= 90 else f"{int(confidence // 5) * 5}-{int(confidence // 5) * 5 + 5}")
            rr = _number(vector.get("planned_rr"))
            rr_band = "MISSING" if rr is None else f"{math.floor(rr * 2) / 2:.1f}-{math.floor(rr * 2) / 2 + .5:.1f}R"
            values = {
                "timeframe": [vector.get("timeframe")],
                "regime": _loads(row.get("regimes_json"), []) or [vector.get("primary_regime")],
                "session": vector.get("session_tags") or [vector.get("session")],
                "weekday_utc": [vector.get("weekday_utc")], "hour_utc": [vector.get("hour_utc")],
                "symbol": [vector.get("symbol")], "direction": [vector.get("direction")],
                "setup_family": [vector.get("setup_family")], "confidence_band": [confidence_band],
                "planned_rr_band": [rr_band],
            }
            for dimension, cohorts in values.items():
                for cohort in cohorts:
                    groups[dimension][str(cohort if cohort is not None else "MISSING")].append(row)
        reports = {dimension: [{"cohort": cohort,
                                **StatisticalResearch.metrics(items, seed_key=f"cohort:{dimension}:{cohort}")}
                               for cohort, items in sorted(cohorts.items())]
                   for dimension, cohorts in groups.items()}
        return {"dimensions": reports, "overall": StatisticalResearch.metrics(rows, seed_key="cohort:overall"),
                "feature_version": FEATURE_VERSION, "overlapping_regimes_and_sessions": True}

    def combination_mining(self, telegram_id: int | None = None,
                           *, rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        rows = self.observations(telegram_id) if rows is None else rows
        minimum = max(3, int(os.getenv("EDGE_COMBINATION_MIN_SAMPLES", os.getenv("EDGE_MIN_SAMPLES", "20"))))
        support_floor = max(0.0, min(float(os.getenv("EDGE_COMBINATION_MIN_SUPPORT", "0.05")), 1.0))
        max_tests = max(10, min(int(os.getenv("EDGE_MAX_COMBINATIONS", "120")), 1000))
        candidates = list(itertools.chain(itertools.combinations(BINARY_FEATURES, 2),
                                          itertools.combinations(BINARY_FEATURES, 3)))[:max_tests]
        findings = []
        for combination in candidates:
            matched = [row for row in rows if all(row["vector"].get(name) is True for name in combination)]
            matched_ids = {int(row["signal_id"]) for row in matched}
            baseline = [row for row in rows if int(row["signal_id"]) not in matched_ids]
            support = len(matched) / len(rows) if rows else 0.0
            if len(matched) < minimum or len(baseline) < minimum or support < support_floor:
                continue
            metrics = StatisticalResearch.metrics(matched, seed_key="+".join(combination))
            baseline_metrics = StatisticalResearch.metrics(baseline, seed_key="baseline:" + "+".join(combination))
            matched_r = [float(row["signal_r"]) for row in matched]
            baseline_r = [float(row["signal_r"]) for row in baseline]
            delta = statistics.fmean(matched_r) - statistics.fmean(baseline_r)
            findings.append({
                "features": list(combination), "sample_size": len(matched),
                "baseline_sample_size": len(baseline), "support": support,
                "metrics": metrics, "baseline": baseline_metrics,
                "expectancy_delta_r": delta,
                "expectancy_delta_interval_95": StatisticalResearch.delta_interval(matched_r, baseline_r),
                "multiple_testing": {"tests_considered": len(candidates),
                                     "bonferroni_alpha": .05 / max(1, len(candidates))},
                "evidence_state": "EXPLORATORY",
            })
        findings.sort(key=lambda item: (-abs(item["expectancy_delta_r"]), -item["sample_size"], item["features"]))
        return {"feature_version": FEATURE_VERSION, "algorithm_version": ALGORITHM_VERSION,
                "tested_combinations": len(candidates), "findings": findings[:50],
                "status": "EXPLORATORY", "out_of_sample_confirmed": False}

    def negative_edge_candidates(self, telegram_id: int | None = None) -> dict[str, Any]:
        rows = self.observations(telegram_id)
        contributions = self.feature_contributions(telegram_id, rows=rows)["features"]
        combinations = self.combination_mining(telegram_id, rows=rows)["findings"]
        cohorts = self.cohort_edges(telegram_id, rows=rows)["dimensions"]
        candidates = []
        for item in contributions:
            interval = item.get("expectancy_delta_interval_95")
            if item["evidence_state"] != "INSUFFICIENT" and interval and interval[1] < 0:
                candidates.append({"filter": {"feature": item["feature"], "equals": True},
                                   "sample_size": item["present"]["sample_size"],
                                   "expectancy_delta_r": item["expectancy_delta_r"],
                                   "interval_95": interval, "status": "POSSIBLE_EXCLUSION_CANDIDATE"})
        for item in combinations:
            interval = item.get("expectancy_delta_interval_95")
            if interval and interval[1] < 0:
                candidates.append({"filter": {"all_true": item["features"]},
                                   "sample_size": item["sample_size"],
                                   "expectancy_delta_r": item["expectancy_delta_r"],
                                   "interval_95": interval, "status": "POSSIBLE_EXCLUSION_CANDIDATE"})
        for dimension, items in cohorts.items():
            for item in items:
                interval = item.get("expectancy_interval_95")
                if item["evidence_state"] != "INSUFFICIENT" and interval and interval[1] < 0:
                    candidates.append({"filter": {"equals": {dimension: item["cohort"]}},
                                       "sample_size": item["sample_size"],
                                       "expectancy_delta_r": item["expectancy_r"],
                                       "interval_95": interval,
                                       "status": "POSSIBLE_EXCLUSION_CANDIDATE"})
        return {"candidates": sorted(candidates, key=lambda item: item["expectancy_delta_r"]),
                "automatic_production_exclusion": False,
                "warning": "Candidates remain research-only until frozen forward confirmation."}

    @staticmethod
    def _persist_finding(finding_type: str, definition: dict[str, Any], comparator: dict[str, Any],
                         metrics: dict[str, Any], dataset_start: str | None,
                         dataset_cutoff: str) -> str:
        key = {"type": finding_type, "feature_version": FEATURE_VERSION,
               "algorithm_version": ALGORITHM_VERSION, "filter": definition,
               "comparator": comparator, "dataset_cutoff": dataset_cutoff}
        finding_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "edge-finding:" + _checksum(key)))
        now = datetime.now(timezone.utc).isoformat()
        with connect() as conn:
            conn.execute("""INSERT INTO research_findings(finding_id,finding_type,feature_version,
                algorithm_version,filter_json,comparator_json,evidence_state,sample_size,
                baseline_sample_size,metrics_json,dataset_start,dataset_cutoff,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(finding_id) DO NOTHING""", (
                finding_id, finding_type, FEATURE_VERSION, ALGORITHM_VERSION,
                _canonical(definition), _canonical(comparator),
                str(metrics.get("evidence_state") or "EXPLORATORY"),
                int(metrics.get("sample_size") or 0), int(metrics.get("baseline_sample_size") or 0),
                _canonical(metrics), dataset_start, dataset_cutoff, now))
        return finding_id

    def refresh_findings(self, telegram_id: int | None = None) -> dict[str, int]:
        rows = self.observations(telegram_id)
        if not rows:
            return {"findings_persisted": 0, "hypotheses_frozen": 0}
        dataset_start, dataset_cutoff = rows[0]["decision_at"], rows[-1]["decision_at"]
        contributions = self.feature_contributions(telegram_id, rows=rows)["features"]
        combinations = self.combination_mining(telegram_id, rows=rows)["findings"]
        persisted = frozen = 0
        candidates: list[tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]] = []
        for item in contributions:
            if item["evidence_state"] == "INSUFFICIENT" or item["expectancy_delta_r"] is None:
                continue
            definition = {"feature": item["feature"], "equals": True}
            metrics = {"sample_size": item["present"]["sample_size"],
                       "baseline_sample_size": item["absent"]["sample_size"],
                       "expectancy_delta_r": item["expectancy_delta_r"],
                       "interval_95": item["expectancy_delta_interval_95"],
                       "evidence_state": "EXPLORATORY"}
            candidates.append(("FEATURE_CONTRIBUTION", definition, {"feature": item["feature"], "equals": False}, metrics))
        for item in combinations:
            candidates.append(("FEATURE_COMBINATION", {"all_true": item["features"]},
                               {"baseline": "ALL_OTHER_ELIGIBLE_SIGNALS"},
                               {"sample_size": item["sample_size"],
                                "baseline_sample_size": item["baseline_sample_size"],
                                "expectancy_delta_r": item["expectancy_delta_r"],
                                "interval_95": item["expectancy_delta_interval_95"],
                                "evidence_state": "EXPLORATORY"}))
        candidates.sort(key=lambda item: (-abs(float(item[3]["expectancy_delta_r"])), _canonical(item[1])))
        max_hypotheses = max(1, min(int(os.getenv("EDGE_MAX_NEW_HYPOTHESES", "10")), 50))
        for finding_type, definition, comparator, metrics in candidates[:max_hypotheses]:
            finding_id = self._persist_finding(finding_type, definition, comparator, metrics,
                                               dataset_start, dataset_cutoff)
            persisted += 1
            frozen += self._freeze_hypothesis(finding_id, definition, comparator, metrics,
                                              dataset_start, dataset_cutoff)
        return {"findings_persisted": persisted, "hypotheses_frozen": frozen}

    @staticmethod
    def _freeze_hypothesis(finding_id: str, definition: dict[str, Any], comparator: dict[str, Any],
                           metrics: dict[str, Any], dataset_start: str | None,
                           dataset_cutoff: str) -> int:
        definition_payload = {"feature_version": FEATURE_VERSION, "filter": definition,
                              "comparator": comparator}
        definition_checksum = _checksum(definition_payload)
        hypothesis_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "edge-hypothesis:" + definition_checksum))
        positive = float(metrics.get("expectancy_delta_r") or 0) >= 0
        phrase = f"{_canonical(definition)} appears {'stronger' if positive else 'weaker'} than its frozen comparator"
        now = datetime.now(timezone.utc).isoformat()
        minimum = max(5, int(os.getenv("EDGE_FORWARD_MIN_SAMPLES", "30")))
        with connect() as conn:
            cur = conn.execute("""INSERT INTO research_hypotheses(hypothesis_id,definition_checksum,
                finding_id,hypothesis_text,lifecycle_state,lifecycle_history_json,evidence_state,feature_version,
                algorithm_version,filter_json,comparator_json,discovery_metrics_json,
                discovery_start,discovery_cutoff,frozen_at,forward_start_at,minimum_forward_samples,
                latest_forward_metrics_json,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(definition_checksum) DO NOTHING""", (
                hypothesis_id, definition_checksum, finding_id, phrase, "FORWARD_TESTING",
                _canonical(["DISCOVERED", "BACKTESTED", "FORWARD_TESTING"]),
                str(metrics.get("evidence_state") or "EXPLORATORY"), FEATURE_VERSION,
                ALGORITHM_VERSION, _canonical(definition), _canonical(comparator), _canonical(metrics),
                dataset_start, dataset_cutoff, now, dataset_cutoff, minimum, "{}", now, now))
        return int(cur.rowcount == 1)

    def evaluate_forward_hypotheses(self, telegram_id: int | None = None, limit: int = 50) -> int:
        with connect() as conn:
            hypotheses = [dict(row) for row in conn.execute("""SELECT * FROM research_hypotheses
                WHERE lifecycle_state IN ('FORWARD_TESTING','CONFIRMED','REJECTED')
                ORDER BY id LIMIT ?""", (max(1, min(int(limit), 200)),)).fetchall()]
        evaluated = 0
        now = datetime.now(timezone.utc).isoformat()
        for hypothesis in hypotheses:
            rows = self.observations(telegram_id, after=hypothesis["forward_start_at"])
            definition = _loads(hypothesis["filter_json"], {})
            matched = [row for row in rows if self._feature_matches(row, definition)]
            matched_ids = {int(row["signal_id"]) for row in matched}
            baseline = [row for row in rows if int(row["signal_id"]) not in matched_ids]
            metrics = StatisticalResearch.metrics(matched, seed_key=hypothesis["hypothesis_id"] + ":forward")
            baseline_metrics = StatisticalResearch.metrics(baseline, seed_key=hypothesis["hypothesis_id"] + ":baseline")
            matched_r = [float(row["signal_r"]) for row in matched]
            baseline_r = [float(row["signal_r"]) for row in baseline]
            delta = statistics.fmean(matched_r) - statistics.fmean(baseline_r) if matched_r and baseline_r else None
            delta_interval = StatisticalResearch.delta_interval(matched_r, baseline_r)
            minimum = int(hypothesis["minimum_forward_samples"])
            lifecycle = "FORWARD_TESTING"
            evidence = "INSUFFICIENT" if len(matched) < minimum else "EXPLORATORY"
            interval = metrics.get("expectancy_interval_95")
            if len(matched) >= minimum and interval and interval[0] > 0 and delta is not None and delta > 0:
                lifecycle, evidence = "CONFIRMED", "SUPPORTED"
            elif len(matched) >= minimum and ((interval and interval[1] < 0) or (delta is not None and delta < 0)):
                lifecycle, evidence = "REJECTED", "REJECTED"
            elif not rows:
                frozen_at = _utc(hypothesis.get("frozen_at"))
                stale_days = max(1, int(os.getenv("EDGE_HYPOTHESIS_STALE_DAYS", "90")))
                if frozen_at and (datetime.now(timezone.utc) - frozen_at).days >= stale_days:
                    lifecycle = "STALE"
            payload = {"hypothesis_metrics": metrics, "baseline_metrics": baseline_metrics,
                       "expectancy_delta_r": delta, "expectancy_delta_interval_95": delta_interval,
                       "sample_size": len(matched), "baseline_sample_size": len(baseline),
                       "feature_definition_frozen": True, "forward_start_at": hypothesis["forward_start_at"],
                       "evaluation_cutoff": rows[-1]["decision_at"] if rows else hypothesis["forward_start_at"]}
            evaluation_checksum = _checksum(payload)
            history = _loads(hypothesis.get("lifecycle_history_json"), [])
            if lifecycle not in history:
                history.append(lifecycle)
            with connect() as conn:
                cur = conn.execute("""INSERT INTO research_hypothesis_evaluations(hypothesis_id,
                    evaluation_checksum,lifecycle_state,evidence_state,sample_size,baseline_sample_size,
                    metrics_json,evaluation_cutoff,evaluated_at) VALUES(?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(hypothesis_id,evaluation_checksum) DO NOTHING""", (
                    hypothesis["hypothesis_id"], evaluation_checksum, lifecycle, evidence,
                    len(matched), len(baseline), _canonical(payload), payload["evaluation_cutoff"], now))
                conn.execute("""UPDATE research_hypotheses SET lifecycle_state=?,lifecycle_history_json=?,evidence_state=?,
                    latest_forward_metrics_json=?,last_evaluated_at=?,updated_at=? WHERE hypothesis_id=?""", (
                    lifecycle, _canonical(history), evidence, _canonical(payload), now, now,
                    hypothesis["hypothesis_id"]))
            evaluated += int(cur.rowcount == 1)
        return evaluated

    @staticmethod
    def hypotheses(telegram_id: int | None = None, limit: int = 20) -> list[dict[str, Any]]:
        del telegram_id  # Hypotheses are aggregate research artifacts and contain no user-private rows.
        with connect() as conn:
            rows = [dict(row) for row in conn.execute("""SELECT * FROM research_hypotheses
                ORDER BY updated_at DESC,id DESC LIMIT ?""", (max(1, min(int(limit), 100)),)).fetchall()]
        for row in rows:
            row["filter"] = _loads(row.get("filter_json"), {})
            row["discovery_metrics"] = _loads(row.get("discovery_metrics_json"), {})
            row["forward_metrics"] = _loads(row.get("latest_forward_metrics_json"), {})
        return rows

    @staticmethod
    def _model_vector(row: dict[str, Any]) -> list[float]:
        vector = row["vector"]
        result = [1.0 if vector.get(name) is True else 0.0 for name in BINARY_FEATURES]
        scales = {"confidence": 100.0, "planned_rr": 5.0, "structural_strength": 100.0,
                  "liquidity_proximity": 10.0, "rsi": 100.0, "macd": 10.0,
                  "momentum_score": 100.0, "ema_slope": 10.0, "atr_pct": 10.0}
        result.extend(float(_number(vector.get(name), 0.0) or 0.0) / scales[name]
                      for name in NUMERIC_MODEL_FEATURES)
        return result

    @staticmethod
    def _sigmoid(value: float) -> float:
        value = max(-35.0, min(35.0, value))
        return 1.0 / (1.0 + math.exp(-value))

    @classmethod
    def _fit_logistic(cls, rows: list[dict[str, Any]]) -> tuple[float, list[float]]:
        vectors = [cls._model_vector(row) for row in rows]
        targets = [1.0 if float(row["signal_r"]) > 0 else 0.0 for row in rows]
        coefficients = [0.0] * (len(vectors[0]) if vectors else len(BINARY_FEATURES) + len(NUMERIC_MODEL_FEATURES))
        intercept = 0.0
        learning_rate = .15
        regularization = .2
        for _ in range(300):
            intercept_gradient = 0.0
            gradients = [0.0] * len(coefficients)
            for vector, target in zip(vectors, targets):
                predicted = cls._sigmoid(intercept + sum(weight * value for weight, value in zip(coefficients, vector)))
                error = predicted - target
                intercept_gradient += error
                for index, value in enumerate(vector):
                    gradients[index] += error * value
            scale = max(1, len(rows))
            intercept -= learning_rate * intercept_gradient / scale
            for index in range(len(coefficients)):
                coefficients[index] -= learning_rate * (gradients[index] / scale + regularization * coefficients[index] / scale)
        return intercept, coefficients

    @classmethod
    def _predict(cls, row: dict[str, Any], intercept: float, coefficients: list[float]) -> float:
        vector = cls._model_vector(row)
        return cls._sigmoid(intercept + sum(weight * value for weight, value in zip(coefficients, vector)))

    @staticmethod
    def _calibration_error(predictions: list[float], targets: list[float]) -> float | None:
        if not predictions:
            return None
        buckets: dict[int, list[tuple[float, float]]] = defaultdict(list)
        for predicted, target in zip(predictions, targets):
            buckets[min(4, int(predicted * 5))].append((predicted, target))
        return sum(len(items) / len(predictions) * abs(
            statistics.fmean(item[0] for item in items) - statistics.fmean(item[1] for item in items))
                   for items in buckets.values())

    def walk_forward(self, telegram_id: int | None = None, *, persist: bool | None = None) -> dict[str, Any]:
        rows = self.observations(telegram_id)
        should_persist = telegram_id is None if persist is None else bool(persist)
        minimum_train = max(5, int(os.getenv("EDGE_WALK_FORWARD_MIN_TRAIN", "60")))
        validation_size = max(3, int(os.getenv("EDGE_WALK_FORWARD_VALIDATION_SIZE", "20")))
        if len(rows) < minimum_train + validation_size:
            return {"status": "INSUFFICIENT", "sample_size": len(rows),
                    "required": minimum_train + validation_size, "folds": [],
                    "model_version": MODEL_VERSION, "random_split_used": False}
        folds = []
        latest_coefficients: dict[str, float] = {}
        cursor = minimum_train
        while cursor < len(rows):
            train = rows[:cursor]
            validate = rows[cursor:cursor + validation_size]
            if len(validate) < 3:
                break
            intercept, coefficients = self._fit_logistic(train)
            predictions = [self._predict(row, intercept, coefficients) for row in validate]
            targets = [1.0 if float(row["signal_r"]) > 0 else 0.0 for row in validate]
            selected = [row for row, probability in zip(validate, predictions) if probability >= .5]
            baseline = StatisticalResearch.metrics(validate, seed_key=f"wf:{cursor}:baseline")
            selected_metrics = StatisticalResearch.metrics(selected, seed_key=f"wf:{cursor}:selected")
            train_predictions = [self._predict(row, intercept, coefficients) for row in train]
            train_targets = [1.0 if float(row["signal_r"]) > 0 else 0.0 for row in train]
            fold = {
                "train_start": train[0]["decision_at"], "train_cutoff": train[-1]["decision_at"],
                "validation_start": validate[0]["decision_at"],
                "validation_cutoff": validate[-1]["decision_at"],
                "training_samples": len(train), "validation_samples": len(validate),
                "brier_score": statistics.fmean((predicted - target) ** 2
                                                 for predicted, target in zip(predictions, targets)),
                "calibration_error": self._calibration_error(predictions, targets),
                "training_brier_score": statistics.fmean((predicted - target) ** 2
                                                          for predicted, target in zip(train_predictions, train_targets)),
                "selected": selected_metrics, "baseline": baseline,
                "coverage": len(selected) / len(validate),
                "degradation_brier": None,
            }
            fold["degradation_brier"] = fold["brier_score"] - fold["training_brier_score"]
            feature_names = list(BINARY_FEATURES) + list(NUMERIC_MODEL_FEATURES)
            coefficient_map = {name: coefficient for name, coefficient in zip(feature_names, coefficients)}
            latest_coefficients = coefficient_map
            provenance = {"release": APP_VERSION, "algorithm_version": ALGORITHM_VERSION,
                          "chronological_split": True, "random_split": False,
                          "dataset_cutoff": validate[-1]["decision_at"]}
            run_payload = {"fold": fold, "intercept": intercept, "coefficients": coefficient_map}
            run_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "research-model:" + _checksum(run_payload)))
            if should_persist:
                with connect() as conn:
                    conn.execute("""INSERT INTO research_model_runs(run_id,model_version,feature_version,
                        target_definition,training_start,training_cutoff,validation_start,validation_cutoff,
                        training_samples,validation_samples,coefficients_json,metrics_json,provenance_json,created_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(run_id) DO NOTHING""", (
                        run_id, MODEL_VERSION, FEATURE_VERSION, TARGET_DEFINITION,
                        fold["train_start"], fold["train_cutoff"], fold["validation_start"],
                        fold["validation_cutoff"], len(train), len(validate),
                        _canonical({"intercept": intercept, "features": coefficient_map}),
                        _canonical(fold), _canonical(provenance), datetime.now(timezone.utc).isoformat()))
            folds.append(fold)
            cursor += validation_size
        return {"status": "EXPLORATORY" if folds else "INSUFFICIENT", "sample_size": len(rows),
                "folds": folds, "model_version": MODEL_VERSION, "feature_version": FEATURE_VERSION,
                "target_definition": TARGET_DEFINITION, "random_split_used": False,
                "importance_summary": [{"feature": name, "coefficient": value}
                                       for name, value in sorted(latest_coefficients.items(),
                                                                 key=lambda item: (-abs(item[1]), item[0]))[:12]],
                "execution_authority": False}

    def confidence_calibration(self, telegram_id: int | None = None) -> dict[str, Any]:
        rows = self.observations(telegram_id)
        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            confidence = _number(row["vector"].get("confidence"))
            if confidence is None or confidence < 50:
                continue
            lower = min(90, int(confidence // 5) * 5)
            label = "90+" if lower >= 90 else f"{lower}-{lower + 5}"
            buckets[label].append(row)
        result = []
        for label in sorted(buckets, key=lambda item: int(item.rstrip("+").split("-")[0])):
            items = buckets[label]
            metrics = StatisticalResearch.metrics(items, seed_key=f"confidence:{label}")
            mean_confidence = statistics.fmean(float(row["vector"]["confidence"]) for row in items)
            observed = metrics["win_rate"]
            result.append({"bucket": label, "mean_reported_confidence": mean_confidence,
                           "observed_win_rate": observed,
                           "calibration_gap": observed - mean_confidence / 100 if observed is not None else None,
                           **metrics})
        return {"buckets": result, "sample_size": sum(len(items) for items in buckets.values()),
                "interpretation": "Diagnostic ordering/calibration only; deterministic confidence is not silently rewritten."}

    def rr_research(self, telegram_id: int | None = None) -> dict[str, Any]:
        rows = self.observations(telegram_id)
        targets = (1.0, 1.5, 2.0, 2.5, 3.0)
        result = []
        for target in targets:
            certain: list[float] = []
            ambiguous = insufficient = 0
            for row in rows:
                mfe = _number(row.get("mfe_r"))
                mae = _number(row.get("mae_r"))
                if mfe is None or mae is None:
                    insufficient += 1
                    continue
                reached, stopped = mfe >= target, mae >= 1
                if reached and stopped:
                    ambiguous += 1
                elif reached:
                    certain.append(target)
                elif stopped:
                    certain.append(-1.0)
                else:
                    insufficient += 1
            metrics = StatisticalResearch.metrics(
                [{"signal_r": value, "mfe_r": None, "mae_r": None} for value in certain],
                seed_key=f"rr:{target}")
            result.append({"target_r": target, "certain_path_samples": len(certain),
                           "intrabar_order_uncertain": ambiguous, "insufficient_path": insufficient,
                           "metrics": metrics})
        return {"policies": result, "existing_policy": StatisticalResearch.metrics(rows, seed_key="existing-policy"),
                "path_rule": "Samples where both target and stop were reachable without ordered events are excluded.",
                "mode": "SHADOW_RESEARCH_ONLY"}

    def exit_research(self, telegram_id: int | None = None) -> dict[str, Any]:
        fixed = self.rr_research(telegram_id)
        rows = self.observations(telegram_id)
        existing = StatisticalResearch.metrics(rows, seed_key="exit:existing")
        policies = [
            {"policy": "EXISTING_TP1_TP2_TP3", "status": existing["evidence_state"], "metrics": existing},
            {"policy": "FULL_FIXED_TP", "status": "SEE_RR_RESEARCH", "metrics": None},
        ]
        for name in ("PARTIALS", "BREAKEVEN_AFTER_THRESHOLD", "TRAILING", "VOLATILITY_STOP",
                     "STRUCTURE_EXIT", "MOMENTUM_EXHAUSTION"):
            policies.append({"policy": name, "status": "INSUFFICIENT_ORDERED_PATH_DATA", "metrics": None})
        return {"policies": policies, "fixed_target_research": fixed["policies"],
                "warning": "No OHLC intrabar ordering is invented; unavailable policies remain unevaluated.",
                "mode": "SHADOW_RESEARCH_ONLY"}

    def scalping_lab(self, telegram_id: int | None = None) -> dict[str, Any]:
        rows = [row for row in self.observations(telegram_id)
                if row["vector"].get("timeframe") in {"1m", "3m", "5m"}]
        fee = max(0.0, float(os.getenv("SCALPING_TAKER_FEE_PCT", "0.05")))
        spread = max(0.0, float(os.getenv("SCALPING_SPREAD_PCT", "0.02")))
        slippage = max(0.0, float(os.getenv("SCALPING_SLIPPAGE_PCT", "0.03")))
        latency = max(0.0, float(os.getenv("SCALPING_LATENCY_PENALTY_PCT", "0.01")))
        cost_pct = 2 * fee + spread + 2 * slippage + latency
        material_multiple = max(1.0, float(os.getenv("SCALPING_MIN_GROSS_COST_MULTIPLE", "1.5")))
        groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            setup = str(row["vector"].get("setup_family") or "UNKNOWN")
            setup_lower = setup.lower()
            family = ("LIQUIDITY_SWEEP" if "liquid" in setup_lower or row["vector"].get("liquidity_sweep") is True
                      else "BREAKOUT" if "break" in setup_lower or row["vector"].get("bos") is True
                      else "MEAN_REVERSION" if "mean" in setup_lower or "RANGE" in str(row["vector"].get("primary_regime"))
                      else "TREND")
            groups[(str(row["vector"]["timeframe"]), family)].append(row)
        reports = []
        minimum = max(10, int(os.getenv("SCALPING_MIN_SAMPLES", "100")))
        for (timeframe, family), items in sorted(groups.items()):
            adjusted = []
            gross_movements = []
            for row in items:
                risk_pct = _number(row["vector"].get("stop_distance_pct"))
                if not risk_pct or risk_pct <= 0:
                    continue
                cost_r = cost_pct / risk_pct
                adjusted.append({**row, "signal_r": float(row["signal_r"]) - cost_r})
                mfe_pct = (_number(row.get("mfe_r"), 0.0) or 0.0) * risk_pct
                gross_movements.append(mfe_pct)
            metrics = StatisticalResearch.metrics(adjusted, seed_key=f"scalp:{timeframe}:{family}")
            gross = statistics.fmean(gross_movements) if gross_movements else None
            material = bool(gross is not None and gross >= cost_pct * material_multiple)
            evidence = metrics["evidence_state"] if len(adjusted) >= minimum and material else "INSUFFICIENT"
            reports.append({"timeframe": timeframe, "strategy_family": family,
                            "after_cost_metrics": metrics, "average_gross_movement_pct": gross,
                            "break_even_movement_pct": cost_pct,
                            "gross_cost_multiple_required": material_multiple,
                            "movement_materially_exceeds_cost": material,
                            "evidence_state": evidence})
        return {"candidates": reports, "roundtrip_cost_pct": cost_pct,
                "minimum_notional": _number(os.getenv("PAPER_MIN_NOTIONAL_USDT",
                                                        os.getenv("PAPER_MIN_NOTIONAL", "5")), 5.0),
                "precision_model": "EXCHANGE_RULES_REQUIRED_AT_EXECUTION",
                "mode": "PAPER_SHADOW_ONLY"}

    def portfolio_edge(self, telegram_id: int | None = None) -> dict[str, Any]:
        rows = self.observations(telegram_id)
        windows: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            stamp = _utc(row.get("decision_at"))
            if not stamp:
                continue
            minute = stamp.minute - stamp.minute % 15
            key = stamp.replace(minute=minute, second=0, microsecond=0).isoformat()
            windows[key].append(row)
        clustered = [items for items in windows.values() if len(items) > 1]
        same_direction = [row for items in clustered if len({item["side"] for item in items}) == 1 for row in items]
        same_symbol = [row for items in clustered if len({item["symbol"] for item in items}) < len(items) for row in items]
        all_metrics = StatisticalResearch.metrics([row for items in clustered for row in items], seed_key="portfolio:clustered")
        direction_metrics = StatisticalResearch.metrics(same_direction, seed_key="portfolio:direction")
        symbol_metrics = StatisticalResearch.metrics(same_symbol, seed_key="portfolio:symbol")
        return {"simultaneous_windows": len(clustered), "clustered": all_metrics,
                "same_direction": direction_metrics, "same_symbol": symbol_metrics,
                "btc_beta": {"status": "DATA_UNAVAILABLE", "reason": "No immutable synchronized return series"},
                "sector_exposure": {"status": "DATA_UNAVAILABLE", "reason": "No versioned sector taxonomy"},
                "automatic_portfolio_optimization": False}

    def ai_comparison(self, telegram_id: int | None = None) -> dict[str, Any]:
        comparison_limit = max(20, min(int(os.getenv("EDGE_AI_COMPARISON_LIMIT", "500")), 900))
        rows = self.observations(telegram_id, limit=comparison_limit)
        by_signal = {int(row["signal_id"]): row for row in rows}
        if not by_signal:
            return {"sample_size": 0, "status": "INSUFFICIENT", "execution_authority": False}
        placeholders = ",".join("?" for _ in by_signal)
        params: list[Any] = list(by_signal)
        owner = ""
        if telegram_id is not None:
            owner = "AND (d.telegram_id IS NULL OR d.telegram_id=?)"
            params.append(telegram_id)
        with connect() as conn:
            decisions = [dict(row) for row in conn.execute(f"""SELECT d.* FROM ai_decisions d
                WHERE d.signal_id IN ({placeholders}) AND d.validation_code='VALID' {owner}
                AND d.id=(SELECT MAX(d2.id) FROM ai_decisions d2 WHERE d2.signal_id=d.signal_id
                    AND d2.validation_code='VALID') ORDER BY d.id""", tuple(params)).fetchall()]
        minimum_train = max(5, int(os.getenv("EDGE_WALK_FORWARD_MIN_TRAIN", "60")))
        validation_size = max(3, int(os.getenv("EDGE_WALK_FORWARD_VALIDATION_SIZE", "20")))
        research_predictions: dict[int, float] = {}
        cursor = minimum_train
        while cursor < len(rows):
            train = rows[:cursor]
            validate = rows[cursor:cursor + validation_size]
            if not validate:
                break
            intercept, coefficients = self._fit_logistic(train)
            research_predictions.update({int(row["signal_id"]): self._predict(row, intercept, coefficients)
                                         for row in validate})
            cursor += validation_size
        comparisons = []
        for decision in decisions:
            outcome = by_signal.get(int(decision["signal_id"]))
            if not outcome:
                continue
            deterministic = bool(decision.get("deterministic_accepted"))
            gpt = str(decision.get("recommended_action")) in {"ACCEPT_REDUCED", "ACCEPT_STANDARD"}
            research_probability = research_predictions.get(int(decision["signal_id"]))
            comparisons.append({"signal_id": decision["signal_id"], "signal_r": outcome["signal_r"],
                                "deterministic": deterministic, "gpt": gpt,
                                "research_probability": research_probability,
                                "research": None if research_probability is None else research_probability >= .5,
                                "deterministic_gpt_agree": deterministic == gpt})
        accepted_gpt = [item for item in comparisons if item["gpt"]]
        accepted_deterministic = [item for item in comparisons if item["deterministic"]]
        research_scored = [item for item in comparisons if item["research"] is not None]
        accepted_research = [item for item in research_scored if item["research"]]
        expectancy = lambda items: statistics.fmean(float(item["signal_r"]) for item in items) if items else None
        return {"sample_size": len(comparisons), "agreement_rate":
                sum(item["deterministic_gpt_agree"] for item in comparisons) / len(comparisons) if comparisons else None,
                "gpt_expectancy_r": expectancy(accepted_gpt),
                "deterministic_expectancy_r": expectancy(accepted_deterministic),
                "research_scored_samples": len(research_scored),
                "research_expectancy_r": expectancy(accepted_research),
                "research_gpt_agreement_rate": (sum(item["research"] == item["gpt"] for item in research_scored)
                                                / len(research_scored) if research_scored else None),
                "research_avoided_losses": sum(not item["research"] and float(item["signal_r"]) < 0
                                               for item in research_scored),
                "research_missed_winners": sum(not item["research"] and float(item["signal_r"]) > 0
                                               for item in research_scored),
                "research_brier_score": (statistics.fmean((float(item["research_probability"]) -
                    (1.0 if float(item["signal_r"]) > 0 else 0.0)) ** 2 for item in research_scored)
                    if research_scored else None),
                "research_quality_model": MODEL_VERSION,
                "research_prediction_split": "CHRONOLOGICAL_WALK_FORWARD",
                "status": "EXPLORATORY" if comparisons else "INSUFFICIENT",
                "future_outcomes_in_ai_context": False, "execution_authority": False}

    def refresh_strategy_selector(self, limit: int = 200) -> int:
        safe = max(1, min(int(limit), 1000))
        history_limit = max(100, min(int(os.getenv("EDGE_HISTORY_LIMIT", "5000")), 50000))
        with connect() as conn:
            snapshots = [dict(row) for row in conn.execute(f"""SELECT r.* FROM research_signal_snapshots r
                WHERE r.capture_quality='DECISION_TIME' AND NOT EXISTS(
                    SELECT 1 FROM research_strategy_recommendations s
                    WHERE s.snapshot_id=r.snapshot_id AND s.selector_version=?)
                ORDER BY r.id LIMIT {safe}""", (SELECTOR_VERSION,)).fetchall()]
            history = [dict(row) for row in conn.execute(f"""SELECT d.snapshot_id,d.strategy_key,d.action,
                r.primary_regime,r.decision_at,r.owner_telegram_id,o.outcome_json FROM research_strategy_decisions d
                JOIN research_signal_snapshots r ON r.snapshot_id=d.snapshot_id
                JOIN research_outcomes o ON o.snapshot_id=r.snapshot_id AND o.id=(
                    SELECT MAX(o2.id) FROM research_outcomes o2 WHERE o2.snapshot_id=r.snapshot_id)
                WHERE r.capture_quality='DECISION_TIME' ORDER BY r.decision_at DESC
                LIMIT {history_limit}""").fetchall()]
            strategy_rows = conn.execute(f"""SELECT d.snapshot_id,d.strategy_key
                FROM research_strategy_decisions d
                JOIN research_signal_snapshots r ON r.snapshot_id=d.snapshot_id
                WHERE r.capture_quality='DECISION_TIME' AND NOT EXISTS(
                    SELECT 1 FROM research_strategy_recommendations s
                    WHERE s.snapshot_id=r.snapshot_id AND s.selector_version=?)
                ORDER BY r.id,d.strategy_key LIMIT {safe * 10}""", (SELECTOR_VERSION,)).fetchall()
        strategies_by_snapshot: dict[str, list[str]] = defaultdict(list)
        for row in strategy_rows:
            strategies_by_snapshot[str(row["snapshot_id"])].append(str(row["strategy_key"]))
        created = 0
        now = datetime.now(timezone.utc).isoformat()
        for snapshot in snapshots:
            groups: dict[str, list[float]] = defaultdict(list)
            for row in history:
                if row["primary_regime"] != snapshot["primary_regime"] or row["decision_at"] >= snapshot["decision_at"]:
                    continue
                snapshot_owner = snapshot.get("owner_telegram_id")
                history_owner = row.get("owner_telegram_id")
                if snapshot_owner not in {None, 0} and history_owner not in {None, 0, snapshot_owner}:
                    continue
                if snapshot_owner in {None, 0} and history_owner not in {None, 0}:
                    continue
                outcome = _loads(row.get("outcome_json"), {})
                eligible, signal_r, _, _ = self._outcome_layers(outcome)
                if eligible and signal_r is not None and row["action"] == "ACCEPT":
                    groups[str(row["strategy_key"])].append(signal_r)
            strategies = strategies_by_snapshot.get(str(snapshot["snapshot_id"]), [])
            rankings = []
            for strategy in strategies:
                values = groups.get(strategy, [])
                shrinkage = sum(values) / (len(values) + 10) if values else 0.0
                tier = StatisticalResearch.sample_tier(len(values))
                rankings.append({"strategy": strategy, "historical_samples": len(values),
                                 "shrunk_expectancy_r": shrinkage, "estimated_quality": tier})
            rankings.sort(key=lambda item: (-item["shrunk_expectancy_r"], -item["historical_samples"], item["strategy"]))
            payload = {"regime": snapshot["primary_regime"], "rankings": rankings,
                       "history_cutoff": snapshot["decision_at"], "execution_authority": False}
            with connect() as conn:
                cur = conn.execute("""INSERT INTO research_strategy_recommendations(snapshot_id,signal_id,
                    selector_version,regime_key,rankings_json,recommendation_checksum,created_at)
                    VALUES(?,?,?,?,?,?,?) ON CONFLICT(snapshot_id,selector_version) DO NOTHING""", (
                    snapshot["snapshot_id"], snapshot["signal_id"], SELECTOR_VERSION,
                    snapshot["primary_regime"], _canonical(payload), _checksum(payload), now))
            created += int(cur.rowcount == 1)
        return created

    def refresh_rankings(self, limit: int = 200) -> int:
        safe = max(1, min(int(limit), 1000))
        history_limit = max(100, min(int(os.getenv("EDGE_HISTORY_LIMIT", "5000")), 50000))
        with connect() as conn:
            snapshots = [dict(row) for row in conn.execute(f"""SELECT r.*,f.vector_json
                FROM research_signal_snapshots r JOIN research_feature_vectors f ON f.snapshot_id=r.snapshot_id
                WHERE r.capture_quality='DECISION_TIME' AND f.feature_version=?
                  AND f.data_quality='TRUSTWORTHY_DECISION_TIME' AND NOT EXISTS(
                    SELECT 1 FROM research_signal_rankings k
                    WHERE k.snapshot_id=r.snapshot_id AND k.rank_version=?)
                ORDER BY r.id LIMIT {safe}""", (FEATURE_VERSION, RANK_VERSION)).fetchall()]
            prior_snapshots = [dict(row) for row in conn.execute(f"""SELECT decision_at,side,symbol,primary_regime
                FROM research_signal_snapshots WHERE capture_quality='DECISION_TIME'
                ORDER BY decision_at DESC LIMIT {history_limit}""").fetchall()]
        all_history = self.observations(limit=history_limit, include_all=True)
        created = 0
        now = datetime.now(timezone.utc).isoformat()
        cost_pct = max(0.0, float(os.getenv("RESEARCH_ESTIMATED_EXECUTION_COST_PCT", "0.19")))
        overlap_minutes = max(1, min(int(os.getenv("EDGE_RANK_OVERLAP_MINUTES", "60")), 1440))
        for snapshot in snapshots:
            vector = _loads(snapshot.get("vector_json"), {})
            snapshot_owner = snapshot.get("owner_telegram_id")
            history = [row for row in all_history if row["decision_at"] < snapshot["decision_at"]
                       and ((snapshot_owner not in {None, 0}
                             and row.get("owner_telegram_id") in {None, 0, snapshot_owner})
                            or (snapshot_owner in {None, 0}
                                and row.get("owner_telegram_id") in {None, 0}))]
            setup = [row for row in history if row["vector"].get("setup_family") == vector.get("setup_family")]
            regime = [row for row in history if row["vector"].get("primary_regime") == vector.get("primary_regime")]
            confidence = [row for row in history if _number(row["vector"].get("confidence")) is not None
                          and _number(vector.get("confidence")) is not None
                          and abs(float(row["vector"]["confidence"]) - float(vector["confidence"])) < 5]
            historical_expectancy = (sum(float(row["signal_r"]) for row in setup) / (len(setup) + 10)) if setup else 0.0
            regime_expectancy = (sum(float(row["signal_r"]) for row in regime) / (len(regime) + 10)) if regime else 0.0
            calibration = (sum(float(row["signal_r"]) > 0 for row in confidence) / len(confidence)) if confidence else .5
            snapshot_time = _utc(snapshot["decision_at"])
            overlaps = sum(bool(snapshot_time and (prior_time := _utc(row["decision_at"]))
                                and 0 < (snapshot_time - prior_time).total_seconds() <= overlap_minutes * 60
                                and row["side"] == snapshot["side"]
                                and (row["symbol"] == snapshot["symbol"]
                                     or row["primary_regime"] == snapshot["primary_regime"]))
                           for row in prior_snapshots)
            overlap_penalty = min(15.0, overlaps * 1.5)
            quality_flags = sum(vector.get(name) is True for name in BINARY_FEATURES)
            components = {
                "historical_expectancy": max(-20.0, min(20.0, historical_expectancy * 10)),
                "confidence_calibration": max(0.0, min(20.0, calibration * 20)),
                "regime_fit": max(-15.0, min(15.0, regime_expectancy * 8)),
                "setup_evidence": min(20.0, quality_flags * 2.0),
                "portfolio_overlap_penalty": -overlap_penalty,
                "estimated_execution_cost_penalty": -min(10.0, cost_pct * 10),
                "data_quality": 15.0,
                "why": [f"setup history n={len(setup)}", f"regime history n={len(regime)}",
                        f"confidence neighbors n={len(confidence)}", f"prior overlaps={overlaps}",
                        f"estimated cost={cost_pct:.3f}%"],
                "history_cutoff": snapshot["decision_at"],
            }
            score = max(0.0, min(100.0, 50 + sum(value for value in components.values()
                                                  if isinstance(value, (int, float)))))
            with connect() as conn:
                cur = conn.execute("""INSERT INTO research_signal_rankings(snapshot_id,signal_id,
                    rank_version,diagnostic_score,components_json,created_at) VALUES(?,?,?,?,?,?)
                    ON CONFLICT(snapshot_id,rank_version) DO NOTHING""", (
                    snapshot["snapshot_id"], snapshot["signal_id"], RANK_VERSION,
                    score, _canonical(components), now))
            created += int(cur.rowcount == 1)
        return created

    def strategy_regime_report(self, telegram_id: int | None = None) -> dict[str, Any]:
        params: list[Any] = []
        owner = ""
        if telegram_id is not None:
            owner = "AND (r.owner_telegram_id IS NULL OR r.owner_telegram_id=0 OR r.owner_telegram_id=?)"
            params.append(telegram_id)
        with connect() as conn:
            rows = [dict(row) for row in conn.execute(f"""SELECT d.strategy_key,d.action,r.primary_regime,
                o.outcome_json FROM research_strategy_decisions d
                JOIN research_signal_snapshots r ON r.snapshot_id=d.snapshot_id
                JOIN research_outcomes o ON o.snapshot_id=r.snapshot_id AND o.id=(
                    SELECT MAX(o2.id) FROM research_outcomes o2 WHERE o2.snapshot_id=r.snapshot_id)
                WHERE r.capture_quality='DECISION_TIME' {owner} ORDER BY r.decision_at""", tuple(params)).fetchall()]
        groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            outcome = _loads(row["outcome_json"], {})
            eligible, signal_r, mfe_r, mae_r = self._outcome_layers(outcome)
            if eligible and signal_r is not None and row["action"] == "ACCEPT":
                groups[(row["strategy_key"], row["primary_regime"])].append(
                    {"signal_r": signal_r, "mfe_r": mfe_r, "mae_r": mae_r})
        reports = [{"strategy": key[0], "regime": key[1],
                    **StatisticalResearch.metrics(items, seed_key=f"strategy:{key[0]}:{key[1]}")}
                   for key, items in sorted(groups.items())]
        return {"results": reports, "selector_version": SELECTOR_VERSION,
                "global_strategy_assumption": False, "execution_authority": False}

    def edge_dashboard(self, telegram_id: int | None = None) -> dict[str, Any]:
        observations = self.observations(telegram_id)
        metrics = StatisticalResearch.metrics(observations, seed_key="edge-dashboard")
        with connect() as conn:
            findings = int(conn.execute("SELECT COUNT(*) n FROM research_findings").fetchone()["n"] or 0)
            states = {str(row["lifecycle_state"]): int(row["n"]) for row in conn.execute(
                "SELECT lifecycle_state,COUNT(*) n FROM research_hypotheses GROUP BY lifecycle_state").fetchall()}
            latest_model = conn.execute("""SELECT run_id,model_version,validation_cutoff,metrics_json
                FROM research_model_runs ORDER BY id DESC LIMIT 1""").fetchone()
        return {"feature_version": FEATURE_VERSION, "algorithm_version": ALGORITHM_VERSION,
                "quality": self.data_quality_report(telegram_id), "overall": metrics,
                "findings": findings, "hypothesis_states": states,
                "latest_walk_forward": ({**dict(latest_model),
                    "metrics": _loads(latest_model["metrics_json"], {})} if latest_model else None),
                "execution_authority": False, "profitability_claim": False}

    def run_cycle(self, limit: int = 200, *, refresh_analysis: bool = True) -> dict[str, int]:
        normalized = self.normalize_pending(limit)
        rankings = self.refresh_rankings(limit)
        selectors = self.refresh_strategy_selector(limit)
        findings = self.refresh_findings() if refresh_analysis else {
            "findings_persisted": 0, "hypotheses_frozen": 0}
        forward = self.evaluate_forward_hypotheses(limit=max(10, limit // 2)) if refresh_analysis else 0
        if refresh_analysis:
            self.walk_forward()
        return {"normalized": normalized, "rankings_v2": rankings,
                "strategy_recommendations": selectors,
                "findings_persisted": findings["findings_persisted"],
                "hypotheses_frozen": findings["hypotheses_frozen"],
                "forward_evaluations": forward}
