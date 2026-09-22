"""Resource-bounded broad-market anomaly detection.

This module produces MARKET ALERTS only.  It has no dependency on the trading
decision or execution stacks and deliberately exposes ``economic_authority`` as
false on every event.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, Iterable, Sequence

from database.database import connect


WINDOW_MINUTES = (1, 3, 5, 15, 30, 60)
OUTCOME_HORIZON_MINUTES = (1, 3, 5, 15, 30, 60, 240)
SCANNER_OUTCOME_COST_MODEL = "scanner-cycle-cost-model-v1"
DISCOVERY_MODES: dict[str, tuple[str, ...]] = {
    "HOT_NOW": (),
    "EARLY_BUILDUP": ("BUILDUP", "EARLY_ANOMALY"),
    "NEW_ANOMALIES": ("EARLY_ANOMALY", "IGNITION", "EARLY_EXPANSION"),
    "STRONGEST_FLOW": ("SPOT_FLOW_LED_MOVE", "MOMENTUM_EXPANSION", "LEVERAGED_BREAKOUT"),
    "OI_BUILDUP": ("BUILDUP", "LEVERAGED_BREAKOUT"),
    "OI_SHOCK": ("OI_SHOCK",),
    "SQUEEZES": ("SHORT_SQUEEZE", "LONG_SQUEEZE"),
    "SHORT_SQUEEZES": ("SHORT_SQUEEZE",),
    "LONG_SQUEEZES": ("LONG_SQUEEZE",),
    "LIQUIDATION_CASCADES": ("LIQUIDATION_CASCADE",),
    "LIQUIDITY_VACUUM": ("LIQUIDITY_VACUUM_MOVE",),
    "CVD_DIVERGENCES": ("CVD_DIVERGENCE", "ABSORPTION"),
    "CROSS_VENUE": ("CROSS_VENUE_DISLOCATION",),
    "EXHAUSTION_WATCH": ("EXHAUSTION", "REVERSAL_RISK", "FAILED_BREAKOUT"),
    "VOLUME_EXPLOSION": ("VOLUME_EXPLOSION",),
    "VOLATILITY_COMPRESSION": ("VOLATILITY_COMPRESSION",),
    "BREAKOUT_IGNITION": (
        "IGNITION", "EXPANSION", "EARLY_EXPANSION", "MOMENTUM_EXPANSION",
        "LEVERAGED_BREAKOUT",
    ),
}

SCANNER_ALERT_TYPES = (
    "PUMP_DUMP", "EARLY_BUILDUP", "OI_SHOCK", "OI_BUILDUP", "FUNDING_EXTREME",
    "LIQUIDATION_CASCADE", "CVD_DIVERGENCE", "CROSS_VENUE_DISLOCATION",
    "SPREAD_EXPANSION", "LIQUIDITY_VACUUM", "VOLUME_EXPLOSION",
    "VOLATILITY_COMPRESSION", "VOLATILITY_BREAKOUT", "EXHAUSTION_TRANSITION",
)


class Severity(StrEnum):
    NORMAL = "NORMAL"
    STRONG = "STRONG"
    EXTREME = "EXTREME"


@dataclass(frozen=True)
class Candle:
    opened_at: datetime
    open: float
    high: float
    low: float
    close: float
    quote_volume: float
    trade_count: int | None = None


@dataclass(frozen=True)
class ScannerSettings:
    enabled: bool = True
    market_scope: str = "ALL_LIQUID"
    custom_symbols: tuple[str, ...] = ()
    windows: tuple[int, ...] = (3, 5, 15, 60)
    move_threshold_pct: float = 3.0
    minimum_quote_volume_24h: float = 20_000_000.0
    minimum_severity: Severity = Severity.NORMAL
    cooldown_seconds: int = 900
    re_alert_pct: float = 2.0
    pump_alerts: bool = True
    dump_alerts: bool = True
    liquidation_alerts: bool = True
    oi_shock_alerts: bool = True
    funding_extreme_alerts: bool = True
    muted_symbols: tuple[str, ...] = ()
    notifications_enabled: bool = True
    quiet_mode: bool = False
    enabled_alert_types: tuple[str, ...] = SCANNER_ALERT_TYPES


@dataclass(frozen=True)
class SymbolSnapshot:
    symbol: str
    venue: str
    observed_at: datetime
    price: float
    change_24h_pct: float | None
    quote_volume_24h: float | None
    changes_pct: dict[int, float]
    start_prices: dict[int, float]
    relative_volume: float | None
    rsi: dict[int, float | None]
    volatility_pct: float | None
    normalized_moves: dict[int, float | None]
    recent_range_position: float | None
    trade_count: int | None = None
    oi_change_pct: float | None = None
    funding_rate: float | None = None
    basis_pct: float | None = None
    cvd: float | None = None
    taker_imbalance: float | None = None
    liquidations_usd: float | None = None
    liquidation_bias: str | None = None
    spread_pct: float | None = None
    book_imbalance: float | None = None
    cross_venue_diff_pct: float | None = None
    trend: str | None = None
    structure: str | None = None
    data_quality: str = "BASIC"
    freshness_seconds: float = 0.0
    robust_zscore: dict[int, float | None] = field(default_factory=dict)
    historical_percentile: dict[int, float | None] = field(default_factory=dict)
    return_velocity_pct: float | None = None
    return_acceleration_pct: float | None = None
    volume_robust_zscore: float | None = None
    distance_from_vwap_pct: float | None = None
    btc_relative_return_pct: float | None = None
    eth_relative_return_pct: float | None = None
    market_relative_return_pct: float | None = None
    cross_sectional_percentile: float | None = None
    range_expansion: float | None = None
    body_to_range: float | None = None
    upper_wick_ratio: float | None = None
    lower_wick_ratio: float | None = None

    def public_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["observed_at"] = self.observed_at.isoformat()
        return result


@dataclass(frozen=True)
class MarketAlert:
    event_id: str
    episode_id: str
    symbol: str
    venue: str
    direction: str
    severity: Severity
    window_minutes: int
    move_pct: float
    price_start: float
    price_end: float
    relative_volume: float | None
    volatility_normalized_move: float | None
    signal_count_24h: int
    observed_at: datetime
    snapshot: SymbolSnapshot
    alert_type: str = "PUMP_DUMP"
    classification: str = "MARKET_ALERT"
    economic_authority: bool = False
    phase: str = "EARLY_ANOMALY"
    market_state: str = "UNKNOWN_MIXED"
    evidence_quality: str = "BASIC"
    reasons: tuple[str, ...] = ()
    risk_flags: tuple[str, ...] = ()

    def public_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["severity"] = self.severity.value
        result["observed_at"] = self.observed_at.isoformat()
        result["snapshot"] = self.snapshot.public_dict()
        return result


def percent_change(start: float, end: float) -> float:
    if not math.isfinite(start) or not math.isfinite(end) or start <= 0:
        raise ValueError("Prices must be finite and start must be positive")
    return (end / start - 1.0) * 100.0


def calculate_rsi(closes: Sequence[float], period: int = 14) -> float | None:
    values = [float(value) for value in closes if math.isfinite(float(value))]
    if len(values) <= period:
        return None
    deltas = [right - left for left, right in zip(values[-period - 1:-1], values[-period:])]
    gains = sum(max(delta, 0.0) for delta in deltas) / period
    losses = sum(max(-delta, 0.0) for delta in deltas) / period
    if losses == 0:
        return 100.0 if gains > 0 else 50.0
    return round(100.0 - (100.0 / (1.0 + gains / losses)), 2)


def _resampled_closes(candles: Sequence[Candle], minutes: int) -> list[float]:
    if minutes <= 1:
        return [float(item.close) for item in candles]
    result: list[float] = []
    for index in range(minutes - 1, len(candles), minutes):
        result.append(float(candles[index].close))
    return result


def robust_zscore(value: float, history: Sequence[float]) -> float | None:
    clean = [float(item) for item in history if math.isfinite(float(item))]
    if len(clean) < 20:
        return None
    median = statistics.median(clean)
    mad = statistics.median(abs(item - median) for item in clean)
    if mad <= 1e-12:
        return None
    return round((float(value) - median) / (1.4826 * mad), 3)


def historical_percentile(value: float, history: Sequence[float]) -> float | None:
    clean = [abs(float(item)) for item in history if math.isfinite(float(item))]
    if len(clean) < 20:
        return None
    magnitude = abs(float(value))
    return round(100 * sum(item <= magnitude for item in clean) / len(clean), 1)


def select_liquid_universe(
    instruments: Iterable[dict[str, Any]], *, minimum_quote_volume: float,
    limit: int = 40,
) -> tuple[str, ...]:
    """Select a deterministic liquid USDT perpetual universe without outcomes."""
    eligible: list[tuple[float, str]] = []
    for item in instruments:
        symbol = str(item.get("symbol") or "").upper()
        status = str(item.get("status") or "TRADING").upper()
        contract = str(item.get("contract_type") or item.get("contractType") or "PERPETUAL").upper()
        volume = float(item.get("quote_volume") or item.get("quoteVolume") or 0.0)
        if (symbol.endswith("USDT") and status == "TRADING" and contract == "PERPETUAL"
                and volume >= minimum_quote_volume and math.isfinite(volume)):
            eligible.append((volume, symbol))
    eligible.sort(key=lambda pair: (-pair[0], pair[1]))
    return tuple(symbol for _, symbol in eligible[:max(1, int(limit))])


def build_symbol_snapshot(
    *, symbol: str, venue: str, candles: Sequence[Candle],
    change_24h_pct: float | None = None, quote_volume_24h: float | None = None,
    enrichment: dict[str, Any] | None = None, observed_at: datetime | None = None,
) -> SymbolSnapshot:
    if len(candles) < 61:
        raise ValueError("At least 61 one-minute candles are required")
    ordered = sorted(candles, key=lambda candle: candle.opened_at)
    price = float(ordered[-1].close)
    changes: dict[int, float] = {}
    starts: dict[int, float] = {}
    for window in WINDOW_MINUTES:
        start = float(ordered[-window - 1].close)
        starts[window] = start
        changes[window] = round(percent_change(start, price), 4)
    returns = [percent_change(left.close, right.close)
               for left, right in zip(ordered[-61:-1], ordered[-60:])]
    volatility = statistics.pstdev(returns) if len(returns) > 1 else 0.0
    normalized = {
        window: (round(abs(change) / (volatility * math.sqrt(window)), 3)
                 if volatility > 0 else None)
        for window, change in changes.items()
    }
    recent_volumes = [max(0.0, float(candle.quote_volume)) for candle in ordered[-61:-1]]
    baseline_volume = statistics.median(recent_volumes) if recent_volumes else 0.0
    relative_volume = (float(ordered[-1].quote_volume) / baseline_volume
                       if baseline_volume > 0 else None)
    recent = ordered[-61:]
    recent_low = min(float(item.low) for item in recent)
    recent_high = max(float(item.high) for item in recent)
    range_position = ((price - recent_low) / (recent_high - recent_low)
                      if recent_high > recent_low else None)
    zscores: dict[int, float | None] = {}
    percentiles: dict[int, float | None] = {}
    closes = [float(item.close) for item in ordered]
    for window, move in changes.items():
        samples = [percent_change(closes[index - window], closes[index])
                   for index in range(window, len(closes) - 1)]
        zscores[window] = robust_zscore(move, samples)
        percentiles[window] = historical_percentile(move, samples)
    current_volume = float(ordered[-1].quote_volume)
    volume_zscore = robust_zscore(current_volume, recent_volumes)
    weighted = sum(float(item.close) * max(0.0, float(item.quote_volume)) for item in recent)
    volume_total = sum(max(0.0, float(item.quote_volume)) for item in recent)
    rolling_vwap = weighted / volume_total if volume_total > 0 else None
    distance_vwap = percent_change(rolling_vwap, price) if rolling_vwap else None
    velocity = returns[-1] if returns else None
    acceleration = returns[-1] - returns[-2] if len(returns) > 1 else None
    latest = ordered[-1]
    latest_range = max(0.0, float(latest.high) - float(latest.low))
    prior_ranges = [max(0.0, float(item.high) - float(item.low)) for item in ordered[-61:-1]]
    baseline_range = statistics.median(prior_ranges) if prior_ranges else 0.0
    body = abs(float(latest.close) - float(latest.open))
    upper_wick = max(0.0, float(latest.high) - max(float(latest.open), float(latest.close)))
    lower_wick = max(0.0, min(float(latest.open), float(latest.close)) - float(latest.low))
    enrich = dict(enrichment or {})
    now = observed_at or datetime.now(timezone.utc)
    last_at = ordered[-1].opened_at
    if last_at.tzinfo is None:
        last_at = last_at.replace(tzinfo=timezone.utc)
    return SymbolSnapshot(
        symbol=symbol.upper(), venue=venue.upper(), observed_at=now, price=price,
        change_24h_pct=change_24h_pct, quote_volume_24h=quote_volume_24h,
        changes_pct=changes, start_prices=starts,
        relative_volume=round(relative_volume, 3) if relative_volume is not None else None,
        rsi={3: calculate_rsi(_resampled_closes(ordered, 3), 14),
             5: calculate_rsi(_resampled_closes(ordered, 5), 14),
             15: calculate_rsi(_resampled_closes(ordered, 15), 14)},
        volatility_pct=round(volatility, 4), normalized_moves=normalized,
        recent_range_position=round(range_position, 4) if range_position is not None else None,
        trade_count=ordered[-1].trade_count,
        oi_change_pct=enrich.get("oi_change_pct"), funding_rate=enrich.get("funding_rate"),
        basis_pct=enrich.get("basis_pct"), cvd=enrich.get("cvd"),
        taker_imbalance=enrich.get("taker_imbalance"),
        liquidations_usd=enrich.get("liquidations_usd"),
        liquidation_bias=enrich.get("liquidation_bias"), spread_pct=enrich.get("spread_pct"),
        book_imbalance=enrich.get("book_imbalance"),
        cross_venue_diff_pct=enrich.get("cross_venue_diff_pct"),
        trend=enrich.get("trend"), structure=enrich.get("structure"),
        data_quality=str(enrich.get("data_quality") or "BASIC"),
        freshness_seconds=max(0.0, (now - last_at).total_seconds()),
        robust_zscore=zscores, historical_percentile=percentiles,
        return_velocity_pct=round(velocity, 4) if velocity is not None else None,
        return_acceleration_pct=round(acceleration, 4) if acceleration is not None else None,
        volume_robust_zscore=volume_zscore,
        distance_from_vwap_pct=round(distance_vwap, 4) if distance_vwap is not None else None,
        btc_relative_return_pct=enrich.get("btc_relative_return_pct"),
        eth_relative_return_pct=enrich.get("eth_relative_return_pct"),
        market_relative_return_pct=enrich.get("market_relative_return_pct"),
        cross_sectional_percentile=enrich.get("cross_sectional_percentile"),
        range_expansion=(round(latest_range / baseline_range, 3) if baseline_range > 0 else None),
        body_to_range=(round(body / latest_range, 3) if latest_range > 0 else None),
        upper_wick_ratio=(round(upper_wick / latest_range, 3) if latest_range > 0 else None),
        lower_wick_ratio=(round(lower_wick / latest_range, 3) if latest_range > 0 else None),
    )


class PumpDumpScanner:
    version = "opportunity-anomaly-scanner-v2"

    @staticmethod
    def _severity(move: float, threshold: float, normalized: float | None,
                  relative_volume: float | None) -> Severity:
        ratio = abs(move) / max(threshold, 0.01)
        if ratio >= 2.5 or (ratio >= 1.8 and (normalized or 0) >= 4 and (relative_volume or 0) >= 3):
            return Severity.EXTREME
        if ratio >= 1.5 or ((normalized or 0) >= 3 and (relative_volume or 0) >= 2):
            return Severity.STRONG
        return Severity.NORMAL

    @staticmethod
    def _interpret(snapshot: SymbolSnapshot, move: float, window: int) -> dict[str, Any]:
        up = move >= 0
        oi = snapshot.oi_change_pct
        flow = snapshot.taker_imbalance
        liq = snapshot.liquidations_usd or 0
        spread = snapshot.spread_pct or 0
        book = snapshot.book_imbalance
        cross = snapshot.cross_venue_diff_pct or 0
        reasons: list[str] = []
        risks: list[str] = []
        phase = "IGNITION"
        state = "MOMENTUM_EXPANSION"

        if abs(move) >= 5 or abs(snapshot.robust_zscore.get(window) or 0) >= 5:
            phase = "EXPANSION"

        if (snapshot.relative_volume or 0) >= 2:
            reasons.append(f"relative volume {snapshot.relative_volume:.2f}x")
        percentile = snapshot.historical_percentile.get(window)
        if percentile is not None:
            reasons.append(f"{percentile:.1f} historical percentile")
        zscore = snapshot.robust_zscore.get(window)
        if zscore is not None:
            reasons.append(f"robust z-score {zscore:+.2f}")
        if snapshot.market_relative_return_pct is not None:
            reasons.append(f"market-relative {snapshot.market_relative_return_pct:+.2f}%")

        if abs(move) < 1.0 and ((snapshot.relative_volume or 0) >= 3 or abs(oi or 0) >= 3):
            phase, state = "BUILDUP", "BUILDUP"
        elif abs(move) < 2.0:
            phase, state = "EARLY_ANOMALY", "EARLY_ANOMALY"
        if abs(oi or 0) >= 3 and flow is not None and ((up and flow > .15) or (not up and flow < -.15)):
            state = "LEVERAGED_BREAKOUT"
            reasons.append(f"OI {oi:+.2f}% confirms directional taker flow")
        elif oi is not None and oi < -2 and liq >= 250_000:
            state = "SHORT_SQUEEZE" if up else "LONG_SQUEEZE"
            reasons.append("OI contraction and forced liquidation flow")
        if liq >= 1_000_000:
            state = "LIQUIDATION_CASCADE"
            phase = "EXPANSION"
            reasons.append(f"liquidations ${liq:,.0f}")
        if spread >= .20 or abs(book or 0) >= .85:
            state = "LIQUIDITY_VACUUM_MOVE"
            reasons.append("spread/depth indicates liquidity withdrawal")
            risks.append("fragile book liquidity")
        if abs(cross) >= .25:
            state = "CROSS_VENUE_DISLOCATION"
            reasons.append(f"cross-venue dispersion {cross:+.3f}%")
            risks.append("venue disagreement")
        if flow is not None and ((up and flow < -.1) or (not up and flow > .1)):
            state = "ABSORPTION"
            reasons.append("price and aggressor flow disagree")
            risks.append("CVD/price divergence")
        if (snapshot.return_acceleration_pct is not None and
                move * snapshot.return_acceleration_pct < 0 and abs(move) >= 5):
            state, phase = "EXHAUSTION", "EXHAUSTION"
            risks.append("flow/return acceleration is decelerating")
        if abs(snapshot.funding_rate or 0) >= .001:
            risks.append("funding extreme")
        if spread >= .10:
            risks.append("spread widening")
        available = sum(value is not None for value in (
            oi, flow, snapshot.cvd, snapshot.spread_pct, book,
            snapshot.cross_venue_diff_pct, snapshot.liquidations_usd,
        ))
        quality = "HIGH" if available >= 5 and snapshot.freshness_seconds <= 90 else (
            "MEDIUM" if available >= 2 and snapshot.freshness_seconds <= 180 else "BASIC"
        )
        return {
            "phase": phase, "market_state": state, "reasons": tuple(reasons),
            "risk_flags": tuple(dict.fromkeys(risks)), "evidence_quality": quality,
        }

    def detect(self, snapshot: SymbolSnapshot, settings: ScannerSettings) -> list[dict[str, Any]]:
        if not settings.enabled or snapshot.symbol in settings.muted_symbols:
            return []
        if (snapshot.quote_volume_24h is None or not math.isfinite(snapshot.quote_volume_24h)
                or snapshot.quote_volume_24h < settings.minimum_quote_volume_24h):
            return []
        if snapshot.freshness_seconds > 180 or snapshot.data_quality.upper() in {
            "STALE", "UNAVAILABLE", "INVALID",
        }:
            return []
        ranks = {Severity.NORMAL: 0, Severity.STRONG: 1, Severity.EXTREME: 2}
        events: list[dict[str, Any]] = []
        for window in settings.windows:
            if window not in snapshot.changes_pct:
                continue
            move = snapshot.changes_pct[window]
            adaptive = (
                abs(snapshot.robust_zscore.get(window) or 0) >= 4
                and (snapshot.relative_volume or 0) >= 2
                and abs(move) >= max(.10, settings.move_threshold_pct * .20)
            )
            buildup = (
                (snapshot.relative_volume or 0) >= 3
                and (abs(snapshot.robust_zscore.get(window) or 0) >= 2 or
                     abs(snapshot.oi_change_pct or 0) >= 3)
                and abs(move) >= .05
            )
            if abs(move) < settings.move_threshold_pct and not adaptive and not buildup:
                continue
            direction = "PUMP" if move > 0 else "DUMP"
            if (direction == "PUMP" and not settings.pump_alerts) or (
                    direction == "DUMP" and not settings.dump_alerts):
                continue
            severity = self._severity(
                move, settings.move_threshold_pct,
                snapshot.normalized_moves.get(window), snapshot.relative_volume,
            )
            if ranks[severity] < ranks[settings.minimum_severity]:
                continue
            interpretation = self._interpret(snapshot, move, window)
            state = interpretation["market_state"]
            phase = interpretation["phase"]
            alert_type = {
                "BUILDUP": "EARLY_BUILDUP", "EARLY_ANOMALY": "EARLY_BUILDUP",
                "LEVERAGED_BREAKOUT": "OI_BUILDUP",
                "LIQUIDATION_CASCADE": "LIQUIDATION_CASCADE",
                "CROSS_VENUE_DISLOCATION": "CROSS_VENUE_DISLOCATION",
                "LIQUIDITY_VACUUM_MOVE": "LIQUIDITY_VACUUM",
                "ABSORPTION": "CVD_DIVERGENCE", "EXHAUSTION": "EXHAUSTION_TRANSITION",
                "REVERSAL_RISK": "EXHAUSTION_TRANSITION",
                "FAILED_BREAKOUT": "EXHAUSTION_TRANSITION",
            }.get(
                state,
                "VOLATILITY_BREAKOUT" if phase in {"IGNITION", "EXPANSION", "EARLY_EXPANSION"}
                else "PUMP_DUMP",
            )
            if alert_type not in settings.enabled_alert_types:
                continue
            events.append({
                "symbol": snapshot.symbol, "venue": snapshot.venue,
                "direction": direction, "severity": severity,
                "window_minutes": window, "move_pct": move,
                "price_start": snapshot.start_prices[window], "price_end": snapshot.price,
                "relative_volume": snapshot.relative_volume,
                "volatility_normalized_move": snapshot.normalized_moves.get(window),
                "snapshot": snapshot, "observed_at": snapshot.observed_at,
                "classification": "MARKET_ALERT", "economic_authority": False,
                "alert_type": alert_type,
                "version": self.version,
                **interpretation,
            })
        return events


class ScannerRepository:
    """Persistent settings, episodes and bounded alert history."""

    @staticmethod
    def _outcome_costs(horizon_minutes: int) -> tuple[float, float, float]:
        """Return explicit round-trip modeled costs for research labels only."""
        fee = max(0.0, float(os.getenv("SCANNER_OUTCOME_FEE_PCT", "0.10")))
        slippage = max(0.0, float(os.getenv("SCANNER_OUTCOME_SLIPPAGE_PCT", "0.08")))
        funding_8h = max(0.0, float(os.getenv("SCANNER_OUTCOME_FUNDING_8H_PCT", "0.01")))
        funding = funding_8h * min(1.0, max(0, horizon_minutes) / 480.0)
        return fee, slippage, funding

    @staticmethod
    def _schedule_outcomes(
        conn: Any, *, event_id: str, symbol: str, direction: str,
        decision_at: datetime, entry_price: float,
    ) -> None:
        created_at = datetime.now(timezone.utc).isoformat()
        decision_text = decision_at.isoformat()
        for horizon in OUTCOME_HORIZON_MINUTES:
            fee, slippage, funding = ScannerRepository._outcome_costs(horizon)
            due_at = (decision_at + timedelta(minutes=horizon)).isoformat()
            conn.execute("""INSERT INTO scanner_outcome_labels(
                event_id,symbol,direction,horizon_minutes,decision_at,due_at,
                entry_price,last_price,max_price,min_price,max_price_at,min_price_at,
                fee_pct,slippage_pct,funding_pct,observation_count,
                observation_resolution,cost_model_version,status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(event_id,horizon_minutes) DO NOTHING""", (
                event_id, symbol, direction, horizon, decision_text, due_at,
                entry_price, entry_price, entry_price, entry_price, decision_text, decision_text,
                fee, slippage, funding, 0, "SCANNER_CYCLE_SAMPLE",
                SCANNER_OUTCOME_COST_MODEL, "PENDING", created_at, created_at,
            ))

    @staticmethod
    def advance_outcomes(snapshot: SymbolSnapshot) -> dict[str, int]:
        """Causally advance pending labels from a later broad-radar observation.

        Extrema are sampled at the scanner cycle resolution; no later feature is
        written back into the immutable decision-time anomaly snapshot.
        """
        now = snapshot.observed_at
        now_text = now.isoformat()
        pending_limit = max(100, min(10_000, int(os.getenv(
            "SCANNER_OUTCOME_ADVANCE_LIMIT", "2000",
        ))))
        updated = finalized = 0
        with connect() as conn:
            rows = [dict(row) for row in conn.execute(
                """SELECT * FROM scanner_outcome_labels
                   WHERE symbol=? AND status='PENDING' AND decision_at<?
                   ORDER BY due_at,event_id LIMIT ?""",
                (snapshot.symbol, now_text, pending_limit),
            ).fetchall()]
            for row in rows:
                entry = float(row["entry_price"])
                price = float(snapshot.price)
                max_price, min_price = float(row["max_price"]), float(row["min_price"])
                max_at, min_at = str(row["max_price_at"]), str(row["min_price_at"])
                if price > max_price:
                    max_price, max_at = price, now_text
                if price < min_price:
                    min_price, min_at = price, now_text
                is_final = now >= datetime.fromisoformat(str(row["due_at"]).replace("Z", "+00:00"))
                values: dict[str, Any] = {
                    "last_price": price, "max_price": max_price, "min_price": min_price,
                    "max_price_at": max_at, "min_price_at": min_at,
                    "observation_count": int(row["observation_count"]) + 1,
                    "updated_at": now_text,
                }
                if is_final:
                    raw_return = percent_change(entry, price)
                    sign = 1.0 if str(row["direction"]) == "PUMP" else -1.0
                    directional = raw_return * sign
                    if sign > 0:
                        mfe = max(0.0, percent_change(entry, max_price))
                        mae = max(0.0, -percent_change(entry, min_price))
                        mfe_at, mae_at = max_at, min_at
                    else:
                        mfe = max(0.0, -percent_change(entry, min_price))
                        mae = max(0.0, percent_change(entry, max_price))
                        mfe_at, mae_at = min_at, max_at
                    decision = datetime.fromisoformat(str(row["decision_at"]).replace("Z", "+00:00"))
                    total_cost = sum(float(row[key]) for key in (
                        "fee_pct", "slippage_pct", "funding_pct",
                    ))
                    values.update({
                        "exit_price": price,
                        "forward_return_pct": raw_return,
                        "directional_return_pct": directional,
                        "mfe_pct": mfe,
                        "mae_pct": mae,
                        "time_to_mfe_seconds": max(0, int((datetime.fromisoformat(mfe_at.replace("Z", "+00:00")) - decision).total_seconds())),
                        "time_to_mae_seconds": max(0, int((datetime.fromisoformat(mae_at.replace("Z", "+00:00")) - decision).total_seconds())),
                        "max_extension_pct": mfe,
                        "retracement_pct": max(0.0, mfe - directional),
                        "net_directional_return_pct": directional - total_cost,
                        "status": "LABELED", "labeled_at": now_text,
                    })
                assignments = ",".join(f"{key}=?" for key in values)
                conn.execute(
                    f"UPDATE scanner_outcome_labels SET {assignments} WHERE id=?",
                    (*values.values(), row["id"]),
                )
                updated += 1
                finalized += int(is_final)
        return {"updated": updated, "finalized": finalized}

    @staticmethod
    def outcome_attribution(
        *, horizon_minutes: int = 15, market_state: str | None = None,
        phase: str | None = None, minimum_samples: int = 30,
    ) -> dict[str, Any]:
        """Descriptive, cost-aware scanner cohort evidence with sample gating."""
        clauses = ["o.status='LABELED'", "o.horizon_minutes=?"]
        params: list[Any] = [int(horizon_minutes)]
        if market_state:
            clauses.append("e.market_state=?")
            params.append(market_state)
        if phase:
            clauses.append("e.phase=?")
            params.append(phase)
        row_limit = max(100, min(20_000, int(os.getenv(
            "SCANNER_ATTRIBUTION_MAX_ROWS", "5000",
        ))))
        params.append(row_limit + 1)
        with connect() as conn:
            rows = [dict(row) for row in conn.execute(f"""SELECT
                o.net_directional_return_pct,o.directional_return_pct,o.mfe_pct,o.mae_pct,
                o.fee_pct,o.slippage_pct,o.funding_pct,o.labeled_at,
                e.symbol,e.market_state,e.phase
                FROM scanner_outcome_labels o JOIN market_anomaly_events e ON e.event_id=o.event_id
                WHERE {' AND '.join(clauses)} ORDER BY o.labeled_at DESC LIMIT ?""",
                tuple(params),
            ).fetchall()]
        truncated = len(rows) > row_limit
        rows = rows[:row_limit]
        values = [float(row["net_directional_return_pct"]) for row in rows]
        adequate = len(values) >= max(1, int(minimum_samples))
        result: dict[str, Any] = {
            "classification": "RESEARCH_SHADOW_EVIDENCE",
            "economic_authority": False,
            "horizon_minutes": int(horizon_minutes),
            "market_state": market_state,
            "phase": phase,
            "sample_size": len(values),
            "minimum_samples": max(1, int(minimum_samples)),
            "sample_adequate": adequate,
            "bounded": True,
            "row_limit": row_limit,
            "truncated": truncated,
            "win_definition": "net directional return after modeled costs > 0",
            "observation_resolution": "SCANNER_CYCLE_SAMPLE",
            "conclusion_status": "DESCRIPTIVE_ONLY" if adequate else "INSUFFICIENT_SAMPLE",
            "predictive_probability": None,
        }
        if not adequate:
            return result
        wins = [value for value in values if value > 0]
        losses = [value for value in values if value < 0]
        gross_profit, gross_loss = sum(wins), abs(sum(losses))
        seed = int(hashlib.sha256(
            f"{horizon_minutes}|{market_state}|{phase}|{len(values)}".encode()
        ).hexdigest()[:16], 16)
        rng = random.Random(seed)
        bootstrap = [statistics.mean(rng.choices(values, k=len(values))) for _ in range(500)]
        bootstrap.sort()
        by_symbol: dict[str, int] = {}
        by_month: dict[str, list[float]] = {}
        for row, value in zip(rows, values):
            symbol = str(row["symbol"])
            by_symbol[symbol] = by_symbol.get(symbol, 0) + 1
            month = str(row["labeled_at"])[:7]
            by_month.setdefault(month, []).append(value)
        total_costs = [sum(float(row[key]) for key in ("fee_pct", "slippage_pct", "funding_pct"))
                       for row in rows]
        gross_directional = [float(row["directional_return_pct"]) for row in rows]
        result.update({
            "mean_net_return_pct": statistics.mean(values),
            "median_net_return_pct": statistics.median(values),
            "win_rate": len(wins) / len(values),
            "false_positive_rate": sum(value <= 0 for value in values) / len(values),
            "false_positive_definition": "net directional return after modeled costs <= 0",
            "profit_factor": gross_profit / gross_loss if gross_loss else None,
            "mean_mfe_pct": statistics.mean(float(row["mfe_pct"]) for row in rows),
            "mean_mae_pct": statistics.mean(float(row["mae_pct"]) for row in rows),
            "bootstrap_mean_95pct_ci": [
                bootstrap[int(len(bootstrap) * .025)], bootstrap[int(len(bootstrap) * .975) - 1],
            ],
            "temporal_stability": {
                key: {"samples": len(items), "mean_net_return_pct": statistics.mean(items)}
                for key, items in by_month.items()
            },
            "symbol_concentration": {
                "largest_symbol_share": max(by_symbol.values()) / len(values),
                "counts": dict(sorted(by_symbol.items())),
            },
            "cost_sensitivity": {
                f"{multiple:g}x": statistics.mean(
                    gross - cost * multiple for gross, cost in zip(gross_directional, total_costs)
                ) for multiple in (.5, 1.0, 2.0)
            },
        })
        return result

    @staticmethod
    def settings(telegram_id: int) -> ScannerSettings:
        with connect() as conn:
            row = conn.execute(
                "SELECT * FROM scanner_user_settings WHERE telegram_id=?", (telegram_id,)
            ).fetchone()
        if not row:
            return ScannerSettings()
        data = dict(row)
        def values(name: str, default: tuple[Any, ...]) -> tuple[Any, ...]:
            try:
                return tuple(json.loads(data.get(name) or "[]"))
            except (TypeError, ValueError):
                return default
        return ScannerSettings(
            enabled=bool(data["enabled"]), market_scope=data["market_scope"],
            custom_symbols=values("custom_symbols_json", ()),
            windows=tuple(int(item) for item in values("windows_json", (3, 5, 15, 60))),
            move_threshold_pct=float(data["move_threshold_pct"]),
            minimum_quote_volume_24h=float(data["minimum_quote_volume_24h"]),
            minimum_severity=Severity(data["minimum_severity"]),
            cooldown_seconds=int(data["cooldown_seconds"]), re_alert_pct=float(data["re_alert_pct"]),
            pump_alerts=bool(data["pump_alerts"]), dump_alerts=bool(data["dump_alerts"]),
            liquidation_alerts=bool(data["liquidation_alerts"]),
            oi_shock_alerts=bool(data["oi_shock_alerts"]),
            funding_extreme_alerts=bool(data["funding_extreme_alerts"]),
            muted_symbols=values("muted_symbols_json", ()),
            notifications_enabled=bool(data.get("notifications_enabled", 1)),
            quiet_mode=bool(data.get("quiet_mode", 0)),
            enabled_alert_types=(
                tuple(str(item).upper() for item in values("alert_types_json", SCANNER_ALERT_TYPES))
                or SCANNER_ALERT_TYPES
            ),
        )

    @staticmethod
    def save_settings(telegram_id: int, settings: ScannerSettings) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with connect() as conn:
            conn.execute("""INSERT INTO scanner_user_settings(
                telegram_id,enabled,market_scope,custom_symbols_json,windows_json,
                move_threshold_pct,minimum_quote_volume_24h,minimum_severity,
                cooldown_seconds,re_alert_pct,pump_alerts,dump_alerts,liquidation_alerts,
                oi_shock_alerts,funding_extreme_alerts,muted_symbols_json,
                notifications_enabled,quiet_mode,alert_types_json,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                enabled=excluded.enabled,market_scope=excluded.market_scope,
                custom_symbols_json=excluded.custom_symbols_json,windows_json=excluded.windows_json,
                move_threshold_pct=excluded.move_threshold_pct,
                minimum_quote_volume_24h=excluded.minimum_quote_volume_24h,
                minimum_severity=excluded.minimum_severity,cooldown_seconds=excluded.cooldown_seconds,
                re_alert_pct=excluded.re_alert_pct,pump_alerts=excluded.pump_alerts,
                dump_alerts=excluded.dump_alerts,liquidation_alerts=excluded.liquidation_alerts,
                oi_shock_alerts=excluded.oi_shock_alerts,
                funding_extreme_alerts=excluded.funding_extreme_alerts,
                muted_symbols_json=excluded.muted_symbols_json,
                notifications_enabled=excluded.notifications_enabled,
                quiet_mode=excluded.quiet_mode,alert_types_json=excluded.alert_types_json,
                updated_at=excluded.updated_at""",
                (telegram_id, int(settings.enabled), settings.market_scope,
                 json.dumps(settings.custom_symbols), json.dumps(settings.windows),
                 settings.move_threshold_pct, settings.minimum_quote_volume_24h,
                 settings.minimum_severity.value, settings.cooldown_seconds, settings.re_alert_pct,
                 int(settings.pump_alerts), int(settings.dump_alerts),
                 int(settings.liquidation_alerts), int(settings.oi_shock_alerts),
                 int(settings.funding_extreme_alerts), json.dumps(settings.muted_symbols),
                 int(settings.notifications_enabled), int(settings.quiet_mode),
                 json.dumps(settings.enabled_alert_types), now))

    @staticmethod
    def subscribers() -> list[tuple[int, ScannerSettings]]:
        with connect() as conn:
            rows = conn.execute("SELECT telegram_id FROM scanner_user_settings WHERE enabled=1").fetchall()
        return [(int(row[0]), ScannerRepository.settings(int(row[0]))) for row in rows]

    @staticmethod
    def recent(
        limit: int = 50, *, telegram_id: int | None = None, mode: str | None = None,
    ) -> list[dict[str, Any]]:
        bounded = max(1, min(200, int(limit)))
        query_limit = min(500, bounded * 5) if mode else bounded
        with connect() as conn:
            if telegram_id is None:
                rows = conn.execute(
                    "SELECT * FROM market_anomaly_events ORDER BY observed_at DESC LIMIT ?",
                    (query_limit,),
                ).fetchall()
            else:
                # New deployments expose the authoritative product-wide radar
                # (telegram_id=0). Fall back to legacy per-user rows only while
                # an upgraded worker has not yet written its first global row.
                rows = conn.execute(
                    "SELECT * FROM market_anomaly_events WHERE telegram_id=0 "
                    "ORDER BY observed_at DESC LIMIT ?", (query_limit,),
                ).fetchall()
                if not rows:
                    rows = conn.execute(
                        "SELECT * FROM market_anomaly_events WHERE telegram_id=? "
                        "ORDER BY observed_at DESC LIMIT ?", (telegram_id, query_limit),
                    ).fetchall()
        result = [dict(row) for row in rows]
        selected_mode = str(mode or "HOT_NOW").upper().replace("-", "_")
        states = DISCOVERY_MODES.get(selected_mode)
        if states:
            result = [row for row in result if row.get("alert_type") in states
                      or row.get("market_state") in states or row.get("phase") in states]
        return result[:bounded]

    @staticmethod
    def event(record_id: int, *, telegram_id: int) -> dict[str, Any] | None:
        with connect() as conn:
            row = conn.execute(
                "SELECT * FROM market_anomaly_events WHERE id=? AND telegram_id IN (0,?)",
                (record_id, telegram_id),
            ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def outcome_counters(*, minimum_samples: int = 30) -> dict[str, int]:
        with connect() as conn:
            pending = conn.execute(
                "SELECT COUNT(*) FROM scanner_outcome_labels WHERE status='PENDING'"
            ).fetchone()
            complete = conn.execute(
                "SELECT COUNT(*) FROM scanner_outcome_labels WHERE status='LABELED'"
            ).fetchone()
            snapshots = conn.execute(
                "SELECT COUNT(DISTINCT event_id) FROM scanner_outcome_labels"
            ).fetchone()
            cohorts = conn.execute(
                """SELECT COUNT(*) FROM (
                    SELECT o.horizon_minutes,e.market_state,e.phase,COUNT(*) AS n
                    FROM scanner_outcome_labels o
                    JOIN market_anomaly_events e ON e.event_id=o.event_id
                    WHERE o.status='LABELED'
                    GROUP BY o.horizon_minutes,e.market_state,e.phase
                    HAVING COUNT(*)>=?
                ) AS qualified""", (max(1, int(minimum_samples)),),
            ).fetchone()
        return {
            "snapshots_collected": int(snapshots[0]) if snapshots else 0,
            "labels_pending": int(pending[0]) if pending else 0,
            "labels_complete": int(complete[0]) if complete else 0,
            "cohorts_sample_ready": int(cohorts[0]) if cohorts else 0,
        }

    @staticmethod
    def home_stats(*, telegram_id: int, now: datetime | None = None) -> dict[str, Any]:
        current = now or datetime.now(timezone.utc)
        cutoff = (current - timedelta(hours=1)).isoformat()
        with connect() as conn:
            active = conn.execute(
                "SELECT COUNT(*) FROM scanner_episode_state WHERE telegram_id=0 AND active=1",
            ).fetchone()
            new_events = conn.execute(
                "SELECT COUNT(*) FROM market_anomaly_events WHERE telegram_id=0 AND observed_at>=?",
                (cutoff,),
            ).fetchone()
            escalations = conn.execute(
                "SELECT COUNT(*) FROM scanner_episode_state "
                "WHERE telegram_id=0 AND last_escalation_at>=?",
                (cutoff,),
            ).fetchone()
            runtime = conn.execute(
                "SELECT * FROM runtime_state WHERE worker_name=?",
                ("pump-dump-market-alert-monitor",),
            ).fetchone()
            operational = conn.execute(
                "SELECT state,heartbeat_at,last_error,rss_mb,child_states_json FROM operational_worker_health "
                "WHERE worker_name='operational_product_worker'"
            ).fetchone()
            forward = conn.execute(
                "SELECT state,heartbeat_at,last_event_at,venues_json,storage_json,last_error "
                "FROM forward_worker_health WHERE worker_name='forward_microstructure_collector'"
            ).fetchone()
        details: dict[str, Any] = {}
        operational_children: dict[str, Any] = {}
        forward_storage: dict[str, Any] = {}
        if runtime:
            try:
                details = json.loads(runtime["details_json"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                details = {}
        if operational:
            try:
                operational_children = json.loads(operational["child_states_json"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                operational_children = {}
        if forward:
            try:
                forward_storage = json.loads(forward["storage_json"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                forward_storage = {}

        def age(value: Any) -> int | None:
            if not value:
                return None
            try:
                seen = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                if seen.tzinfo is None:
                    seen = seen.replace(tzinfo=timezone.utc)
                return max(0, int((current - seen).total_seconds()))
            except (TypeError, ValueError):
                return None

        interval = max(30, int(os.getenv("PUMP_SCANNER_INTERVAL_SECONDS", "60")))
        scanner_heartbeat_age = age(runtime["last_started_at"]) if runtime else None
        scanner_success_age = age(runtime["last_success_at"]) if runtime else None
        radar_age = age((details.get("pipeline_timestamps") or {}).get(
            "broad_radar_completed_at"
        ))
        episode_age = age((details.get("pipeline_timestamps") or {}).get(
            "episode_engine_completed_at"
        ))
        operational_age = age(operational["heartbeat_at"]) if operational else None
        forward_age = age(forward["heartbeat_at"]) if forward else None
        target = int(details.get("universe_target") or resource_budget()["universe_limit"])
        monitored = details.get("universe")
        baseline_ready = int(details.get("baseline_ready_symbols") or 0)
        failed_count = int(details.get("failed_symbol_count") or 0)
        successfully_fetched = int(
            details.get("successfully_fetched") or details.get("snapshots") or 0
        )
        estimated_readiness = (
            0 if monitored is not None and baseline_ready >= int(monitored)
            else max(1, math.ceil(interval / 60))
            if monitored is not None and failed_count == 0 else None
        )
        status = "NOT_STARTED"
        reason = "No scanner runtime checkpoint exists yet."
        runtime_status = str(details.get("status") or "UNKNOWN").upper()
        enrichment_status = str(details.get("enrichment_status") or "UNKNOWN").upper()
        provider_coverage = str(details.get("provider_coverage") or "UNKNOWN").upper()
        scanner_providers = details.get("providers") or {}
        cycle_has_progress = (
            runtime_status == "OK"
            and successfully_fetched > 0
            and baseline_ready > 0
            and not (runtime["last_error"] if runtime else None)
        )
        # A completed cycle with usable histories is authoritative evidence of
        # scanner capability. Preserve provider-local degradation, but never
        # project the contradictory aggregate state UNAVAILABLE.
        if cycle_has_progress and provider_coverage == "UNAVAILABLE":
            provider_coverage = "PARTIAL"
        if runtime_status == "DISABLED":
            status, reason = "FAILED", "Scanner is disabled in the operational worker."
        elif not runtime:
            pass
        elif operational_age is None or operational_age > interval * 5:
            status, reason = "FAILED", "Operational-worker heartbeat is unavailable or stale."
        elif scanner_heartbeat_age is None or scanner_heartbeat_age > interval * 5:
            status, reason = "FAILED", "Scanner loop has not started within five cycles."
        elif provider_coverage == "UNAVAILABLE":
            status, reason = "FAILED", "No viable scanner market-data provider is available."
        elif runtime["last_error"] or scanner_success_age is None:
            status = "WARMING" if scanner_success_age is None and not runtime["last_error"] else "DEGRADED"
            reason = str(runtime["last_error"] or "First broad-radar cycle is still warming.")
        elif scanner_success_age >= interval * 5:
            status, reason = "FAILED", "No successful broad-radar cycle within five intervals."
        elif scanner_success_age >= interval * 2:
            status, reason = "DEGRADED", "Last successful broad-radar cycle is older than two intervals."
        elif provider_coverage == "PARTIAL":
            status = "DEGRADED"
            reason = "Broad Scanner is current and running with partial provider coverage."
        elif successfully_fetched > 0 and failed_count > 0:
            status = "DEGRADED"
            reason = "Broad Scanner completed with partial symbol coverage."
        elif monitored is not None and baseline_ready < int(monitored):
            status, reason = "WARMING", "Some symbols lack the required 1-minute history backfill."
        elif enrichment_status == "DEGRADED":
            status = "DEGRADED"
            reason = "Broad Scanner is current; optional forward microstructure enrichment is unavailable."
        else:
            status, reason = "RUNNING", "Operational heartbeat and broad-radar cycle are current."

        venues: dict[str, Any] = {}
        if forward:
            try:
                venues = json.loads(forward["venues_json"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                venues = {}
        venue_healthy = sum(
            str(value.get("state") or "").upper() == "HEALTHY" for value in venues.values()
        )
        counters = ScannerRepository.outcome_counters()
        scanner_task = operational_children.get("pump_dump_monitor") or {}
        forward_supervisor = forward_storage.get("supervisor") or {}
        result = {
            "scanner_status": status,
            "scanner_status_reason": reason,
            "monitored_symbols": details.get("universe"),
            "universe_target": target,
            "universe_candidates": details.get("universe_candidates"),
            "eligible_liquid_symbols": details.get("eligible_liquid_symbols"),
            "successfully_fetched": successfully_fetched,
            "failed_symbol_count": failed_count,
            "failed_symbols": details.get("failed_symbols") or {},
            "provider_coverage": provider_coverage,
            "provider_operability": (
                "UNAVAILABLE" if provider_coverage == "UNAVAILABLE"
                else "RUNNING_PARTIAL" if provider_coverage == "PARTIAL"
                else "RUNNING_FULL" if provider_coverage == "FULL"
                else "UNKNOWN"
            ),
            "viable_provider_count": max(
                1 if cycle_has_progress else 0,
                int(details.get("viable_provider_count") or 0),
            ),
            "scanner_providers": scanner_providers,
            "baseline_ready_symbols": baseline_ready,
            "baseline_required_minutes": int(details.get("baseline_required_minutes") or 60),
            "baseline_source": details.get("baseline_source"),
            "estimated_readiness_minutes": estimated_readiness,
            "shortlisted_symbols": int(details.get("shortlisted_symbols") or 0),
            "enrichment_requested_symbols": int(details.get("enrichment_requested_symbols") or 0),
            "deep_enrichment_symbols": int(details.get("deep_enrichment_symbols") or 0),
            "enrichment_status": details.get("enrichment_status") or "UNKNOWN",
            "forward_microstructure_state": details.get("forward_microstructure_state") or "UNKNOWN",
            "active_episodes": int(active[0]) if active else 0,
            "new_anomalies_1h": int(new_events[0]) if new_events else 0,
            "escalations_1h": int(escalations[0]) if escalations else 0,
            "scanner_heartbeat_age_seconds": scanner_heartbeat_age,
            "scanner_last_success_age_seconds": scanner_success_age,
            "broad_radar_age_seconds": radar_age,
            "episode_engine_age_seconds": episode_age,
            "collector_freshness_seconds": scanner_success_age,
            "cycle_duration_seconds": details.get("cycle_duration_seconds"),
            "cycle_started_at": details.get("cycle_started_at") or runtime["last_started_at"] if runtime else None,
            "cycle_completed_at": details.get("cycle_completed_at") or runtime["last_finished_at"] if runtime else None,
            "scanner_task_state": scanner_task.get("task_state") or "UNKNOWN",
            # The supervisor stage is the live source. The persisted successful
            # cycle is newer authority than a nested child checkpoint retained
            # by an earlier heartbeat.
            "scanner_current_stage": (
                scanner_task.get("current_stage") or details.get("current_stage")
                or (scanner_task.get("scanner") or {}).get("current_stage") or "UNKNOWN"
            ),
            "scanner_restart_count": int(scanner_task.get("restart_count") or 0),
            "scanner_last_restart_reason": scanner_task.get("last_restart_reason"),
            "pipeline_timestamps": details.get("pipeline_timestamps") or {},
            "operational_worker_state": operational["state"] if operational else "NOT_STARTED",
            "operational_process_state": operational["state"] if operational else "NOT_STARTED",
            "operational_supervisor_state": (
                "RUNNING" if operational_age is not None and operational_age <= 30 else "STALE"
            ),
            "operational_worker_heartbeat_age_seconds": operational_age,
            "operational_worker_rss_mb": operational["rss_mb"] if operational else None,
            "forward_collector_state": forward["state"] if forward else "NOT_STARTED",
            "forward_process_state": forward["state"] if forward else "NOT_STARTED",
            "forward_supervisor_state": forward_supervisor.get("task_state") or "UNKNOWN",
            "forward_restart_count": int(forward_supervisor.get("restart_count") or 0),
            "forward_last_restart_reason": forward_supervisor.get("last_restart_reason"),
            "forward_collector_heartbeat_age_seconds": forward_age,
            "forward_last_event_age_seconds": age(forward["last_event_at"]) if forward else None,
            "venues": venues,
            "venues_healthy": venue_healthy,
            "venues_degraded": max(0, len(venues) - venue_healthy),
            "last_error": runtime["last_error"] if runtime else None,
            "collector_state": runtime_status if runtime else "NOT_STARTED",
        }
        result.update(counters)
        return result

    @staticmethod
    def historical_context(symbol: str, market_state: str, *, minimum_samples: int = 10) -> dict[str, Any]:
        with connect() as conn:
            rows = [dict(row) for row in conn.execute(
                """SELECT episode_id,move_pct,phase,market_state,observed_at
                   FROM market_anomaly_events WHERE symbol=? AND market_state=?
                   ORDER BY observed_at DESC LIMIT 500""",
                (symbol.upper(), market_state),
            ).fetchall()]
        episodes: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            episodes.setdefault(str(row["episode_id"]), []).append(row)
        maxima = [max(abs(float(item["move_pct"])) for item in episode)
                  for episode in episodes.values() if episode]
        durations = []
        for episode in episodes.values():
            times = [datetime.fromisoformat(str(item["observed_at"])) for item in episode]
            if times:
                durations.append((max(times) - min(times)).total_seconds() / 60)
        adequate = len(episodes) >= max(1, int(minimum_samples))
        return {
            "symbol": symbol.upper(), "market_state": market_state,
            "similar_episode_count": len(episodes), "sample_adequate": adequate,
            "typical_max_extension_pct": (
                round(statistics.median(maxima), 3) if adequate and maxima else None
            ),
            "typical_duration_minutes": (
                round(statistics.median(durations), 1) if adequate and durations else None
            ),
            "predictive_probability": None,
        }

    @staticmethod
    def stats_24h(symbol: str | None = None, *, telegram_id: int | None = None,
                  now: datetime | None = None) -> dict[str, Any]:
        current = now or datetime.now(timezone.utc)
        cutoff = (current - timedelta(hours=24)).isoformat()
        sql = "SELECT direction,move_pct,observed_at FROM market_anomaly_events WHERE observed_at>=?"
        params: tuple[Any, ...] = (cutoff,)
        if telegram_id is not None:
            sql += " AND telegram_id=0"
        if symbol:
            sql += " AND symbol=?"
            params += (symbol.upper(),)
        sql += " ORDER BY observed_at"
        with connect() as conn:
            rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
        times = [datetime.fromisoformat(row["observed_at"]) for row in rows]
        gaps = [(right - left).total_seconds() / 60 for left, right in zip(times[:-1], times[1:])]
        pumps = [float(row["move_pct"]) for row in rows if row["direction"] == "PUMP"]
        dumps = [float(row["move_pct"]) for row in rows if row["direction"] == "DUMP"]
        return {
            "signals": len(rows), "pump_events": len(pumps), "dump_events": len(dumps),
            "largest_pump_pct": max(pumps) if pumps else None,
            "largest_dump_pct": min(dumps) if dumps else None,
            "average_minutes_between_episodes": round(sum(gaps) / len(gaps), 1) if gaps else None,
            "predictive_claim": False,
        }

    @staticmethod
    def record_auxiliary(snapshot: SymbolSnapshot, alert: dict[str, Any], *, telegram_id: int = 0) -> bool:
        bucket = int(snapshot.observed_at.timestamp()) // 900
        raw = f"{telegram_id}|{snapshot.venue}|{snapshot.symbol}|{alert['alert_type']}|{bucket}"
        event_id = hashlib.sha256(raw.encode()).hexdigest()
        move = float(snapshot.changes_pct.get(5) or snapshot.changes_pct.get(1) or 0)
        direction = "PUMP" if move >= 0 else "DUMP"
        with connect() as conn:
            result = conn.execute("""INSERT INTO market_anomaly_events(
                event_id,telegram_id,episode_id,symbol,venue,alert_type,direction,severity,window_minutes,
                move_pct,price_start,price_end,relative_volume,normalized_move,
                signal_count_24h,snapshot_json,classification,economic_authority,observed_at,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(event_id) DO NOTHING""",
                (event_id, telegram_id, event_id[:20], snapshot.symbol, snapshot.venue, alert["alert_type"],
                 direction, "STRONG", 5, move, snapshot.start_prices.get(5, snapshot.price),
                 snapshot.price, snapshot.relative_volume, snapshot.normalized_moves.get(5), 1,
                 json.dumps(snapshot.public_dict(), sort_keys=True), "MARKET_ALERT", 0,
                 snapshot.observed_at.isoformat(), datetime.now(timezone.utc).isoformat()))
            if result.rowcount > 0:
                ScannerRepository._schedule_outcomes(
                    conn, event_id=event_id, symbol=snapshot.symbol, direction=direction,
                    decision_at=snapshot.observed_at, entry_price=snapshot.price,
                )
        return result.rowcount > 0

    @staticmethod
    def close_inactive(*, older_than: datetime) -> int:
        with connect() as conn:
            result = conn.execute(
                """UPDATE scanner_episode_state SET active=0,end_state='CLOSED',
                   previous_phase=current_phase,current_phase='CLOSED'
                   WHERE active=1 AND last_seen_at<?""",
                (older_than.isoformat(),),
            )
        return max(0, int(result.rowcount or 0))

    def admit(self, event: dict[str, Any], settings: ScannerSettings, *, telegram_id: int = 0) -> MarketAlert | None:
        now: datetime = event["observed_at"]
        key = (event["venue"], event["symbol"], event["direction"], event["window_minutes"])
        with connect() as conn:
            row = conn.execute("""SELECT * FROM scanner_episode_state WHERE telegram_id=? AND
                venue=? AND symbol=? AND direction=? AND window_minutes=?""", (telegram_id, *key)).fetchone()
            previous = dict(row) if row else None
            severity_rank = {"NORMAL": 0, "STRONG": 1, "EXTREME": 2}
            alert = True
            if previous and previous["active"]:
                elapsed = (now - datetime.fromisoformat(previous["last_alert_at"])).total_seconds()
                escalation = severity_rank[event["severity"].value] > severity_rank[previous["severity"]]
                extension = abs(event["move_pct"]) >= abs(float(previous["peak_move_pct"])) + settings.re_alert_pct
                phase_change = event["phase"] != (previous.get("current_phase") or "EARLY_ANOMALY")
                state_change = event["market_state"] != (previous.get("market_state") or "UNKNOWN_MIXED")
                alert = escalation or extension or phase_change or state_change or elapsed >= settings.cooldown_seconds
                episode_id = previous["episode_id"]
                started_at = previous["started_at"]
                peak = max(abs(event["move_pct"]), abs(float(previous["peak_move_pct"])))
                count = int(previous["alert_count"]) + (1 if alert else 0)
            else:
                raw = f"{telegram_id}|{key}|{now.isoformat()}"
                episode_id = hashlib.sha256(raw.encode()).hexdigest()[:20]
                started_at, peak, count = now.isoformat(), abs(event["move_pct"]), 1
            last_alert = now.isoformat() if alert else previous["last_alert_at"]
            conn.execute("""INSERT INTO scanner_episode_state(
                telegram_id,venue,symbol,direction,window_minutes,episode_id,started_at,last_seen_at,
                last_alert_at,peak_move_pct,severity,alert_count,active)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1)
                ON CONFLICT(telegram_id,venue,symbol,direction,window_minutes) DO UPDATE SET
                episode_id=excluded.episode_id,started_at=excluded.started_at,
                last_seen_at=excluded.last_seen_at,last_alert_at=excluded.last_alert_at,
                peak_move_pct=excluded.peak_move_pct,severity=excluded.severity,
                alert_count=excluded.alert_count,active=1""",
                (telegram_id, *key, episode_id, started_at, now.isoformat(), last_alert, peak,
                 event["severity"].value, count))
            previous_phase = previous.get("current_phase") if previous else None
            peak_severity = event["severity"].value
            if previous and severity_rank.get(str(previous.get("peak_severity")), 0) > severity_rank[peak_severity]:
                peak_severity = str(previous["peak_severity"])
            last_escalation = (
                now.isoformat() if alert else (previous.get("last_escalation_at") if previous else None)
            )
            conn.execute("""UPDATE scanner_episode_state SET
                current_phase=?,previous_phase=?,market_state=?,peak_severity=?,
                last_escalation_at=?,data_quality=?,end_state=NULL
                WHERE telegram_id=? AND venue=? AND symbol=? AND direction=? AND window_minutes=?""",
                (event["phase"], previous_phase, event["market_state"], peak_severity,
                 last_escalation, event["evidence_quality"], telegram_id, *key))
            if not alert:
                return None
            cutoff = (now - timedelta(hours=24)).isoformat()
            count_row = conn.execute("""SELECT COUNT(*) FROM market_anomaly_events
                WHERE telegram_id=? AND symbol=? AND observed_at>=?""",
                (telegram_id, event["symbol"], cutoff)).fetchone()
            signal_count = int(count_row[0]) + 1
            raw_id = f"{episode_id}|{now.isoformat()}|{event['move_pct']}"
            event_id = hashlib.sha256(raw_id.encode()).hexdigest()
            snapshot: SymbolSnapshot = event["snapshot"]
            conn.execute("""INSERT INTO market_anomaly_events(
                event_id,telegram_id,episode_id,symbol,venue,alert_type,direction,severity,window_minutes,
                move_pct,price_start,price_end,relative_volume,normalized_move,
                signal_count_24h,snapshot_json,classification,economic_authority,phase,market_state,
                evidence_quality,reasons_json,risk_flags_json,observed_at,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(event_id) DO NOTHING""",
                (event_id, telegram_id, episode_id, event["symbol"], event["venue"], event["alert_type"],
                 event["direction"], event["severity"].value, event["window_minutes"],
                 event["move_pct"], event["price_start"], event["price_end"],
                 event["relative_volume"], event["volatility_normalized_move"], signal_count,
                 json.dumps(snapshot.public_dict(), sort_keys=True), "MARKET_ALERT", 0,
                 event["phase"], event["market_state"], event["evidence_quality"],
                 json.dumps(event["reasons"]), json.dumps(event["risk_flags"]),
                 now.isoformat(), datetime.now(timezone.utc).isoformat()))
            self._schedule_outcomes(
                conn, event_id=event_id, symbol=event["symbol"], direction=event["direction"],
                decision_at=now, entry_price=snapshot.price,
            )
        return MarketAlert(
            event_id=event_id, episode_id=episode_id, symbol=event["symbol"],
            venue=event["venue"], direction=event["direction"], severity=event["severity"],
            window_minutes=event["window_minutes"], move_pct=event["move_pct"],
            price_start=event["price_start"], price_end=event["price_end"],
            relative_volume=event["relative_volume"],
            volatility_normalized_move=event["volatility_normalized_move"],
            signal_count_24h=signal_count, observed_at=now, snapshot=snapshot,
            phase=event["phase"], market_state=event["market_state"],
            evidence_quality=event["evidence_quality"], reasons=event["reasons"],
            risk_flags=event["risk_flags"],
            alert_type=event["alert_type"],
        )


def classify_additional_alerts(snapshot: SymbolSnapshot) -> tuple[dict[str, Any], ...]:
    """Optional anomaly labels; never trade signals."""
    candidates: list[tuple[str, bool, dict[str, Any]]] = [
        ("OI_SHOCK", abs(snapshot.oi_change_pct or 0) >= 5, {"oi_change_pct": snapshot.oi_change_pct}),
        ("FUNDING_EXTREME", abs(snapshot.funding_rate or 0) >= .001,
         {"funding_rate": snapshot.funding_rate}),
        ("LIQUIDATION_CASCADE", (snapshot.liquidations_usd or 0) >= 1_000_000,
         {"liquidations_usd": snapshot.liquidations_usd, "bias": snapshot.liquidation_bias}),
        ("CVD_DIVERGENCE", abs(snapshot.cvd or 0) >= 1_000_000,
         {"cvd": snapshot.cvd, "move_5m": snapshot.changes_pct.get(5)}),
        ("CROSS_VENUE_DISLOCATION", abs(snapshot.cross_venue_diff_pct or 0) >= .25,
         {"cross_venue_diff_pct": snapshot.cross_venue_diff_pct}),
        ("SPREAD_EXPANSION", (snapshot.spread_pct or 0) >= .20, {"spread_pct": snapshot.spread_pct}),
        ("LIQUIDITY_VACUUM", abs(snapshot.book_imbalance or 0) >= .85,
         {"book_imbalance": snapshot.book_imbalance}),
        ("VOLUME_EXPLOSION", (snapshot.relative_volume or 0) >= 4,
         {"relative_volume": snapshot.relative_volume}),
        ("VOLATILITY_BREAKOUT", max(snapshot.normalized_moves.values() or [0],
         key=lambda value: value or 0) is not None and
         max((value or 0) for value in snapshot.normalized_moves.values()) >= 4,
         {"max_normalized_move": max((value or 0) for value in snapshot.normalized_moves.values())}),
        ("VOLATILITY_COMPRESSION", (snapshot.range_expansion or 1) <= .55
         and abs(snapshot.changes_pct.get(5) or 0) <= 1
         and (snapshot.relative_volume or 0) >= 1.5,
         {"range_expansion": snapshot.range_expansion,
          "relative_volume": snapshot.relative_volume}),
    ]
    return tuple({"alert_type": kind, "classification": "MARKET_ALERT",
                  "economic_authority": False, **details}
                 for kind, active, details in candidates if active)


def render_alert_card(alert: MarketAlert) -> str:
    snapshot = alert.snapshot
    icon = "🟢" if alert.direction == "PUMP" else "🔴"
    lines = [
        f"{icon} <b>{alert.symbol} · {alert.phase.replace('_', ' ')}</b>",
        "<i>MARKET ALERT · never execution authority</i>",
        f"<b>{alert.move_pct:+.2f}% / {alert.window_minutes}m</b>",
        f"<code>{alert.price_start:.8g} → {alert.price_end:.8g}</code>", "",
        f"State: <b>{alert.market_state.replace('_', ' ')}</b>",
        f"Severity / evidence: <b>{alert.severity.value} / {alert.evidence_quality}</b>",
    ]
    optional = [
        ("Volume", f"{snapshot.relative_volume:.2f}× normal" if snapshot.relative_volume is not None else None),
        ("RSI 3m", snapshot.rsi.get(3)), ("RSI 5m", snapshot.rsi.get(5)),
        ("RSI 15m", snapshot.rsi.get(15)),
        ("OI", f"{snapshot.oi_change_pct:+.2f}%" if snapshot.oi_change_pct is not None else None),
        ("Funding", f"{snapshot.funding_rate:.4%}" if snapshot.funding_rate is not None else None),
        ("CVD", f"{snapshot.cvd:+,.0f}" if snapshot.cvd is not None else None),
        ("Liquidations", (f"${snapshot.liquidations_usd:,.0f} · {snapshot.liquidation_bias}"
                           if snapshot.liquidations_usd is not None else None)),
        ("Spread", f"{snapshot.spread_pct:.3f}%" if snapshot.spread_pct is not None else None),
        ("Cross-venue", (f"{snapshot.cross_venue_diff_pct:+.3f}%"
                         if snapshot.cross_venue_diff_pct is not None else None)),
        ("Abnormality", (f"{snapshot.historical_percentile.get(alert.window_minutes):.1f}th percentile"
                         if snapshot.historical_percentile.get(alert.window_minutes) is not None else None)),
        ("BTC relative", (f"{snapshot.btc_relative_return_pct:+.2f}%"
                          if snapshot.btc_relative_return_pct is not None else None)),
        ("Market relative", (f"{snapshot.market_relative_return_pct:+.2f}%"
                             if snapshot.market_relative_return_pct is not None else None)),
    ]
    lines.extend(f"{label}: {value}" for label, value in optional if value is not None)
    if alert.reasons:
        lines += ["", "<b>WHY IS THIS HERE?</b>", *[f"• {reason}" for reason in alert.reasons]]
    if alert.risk_flags:
        lines += ["", "<b>Risk flags</b>", *[f"• {risk}" for risk in alert.risk_flags]]
    lines += ["", f"Signals 24h: <b>{alert.signal_count_24h}</b>",
              f"Exchange: {alert.venue}", f"Data: {snapshot.data_quality} · {snapshot.freshness_seconds:.0f}s old",
              f"Time: {alert.observed_at.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}", "",
              "ℹ️ Market intelligence only — not an approved trade signal."]
    return "\n".join(lines)


def resource_budget() -> dict[str, Any]:
    universe = max(5, int(os.getenv("PUMP_SCANNER_UNIVERSE_LIMIT", "40")))
    interval = max(30, int(os.getenv("PUMP_SCANNER_INTERVAL_SECONDS", "60")))
    return {
        "universe_limit": universe,
        "scan_interval_seconds": interval,
        "bulk_ticker_requests_per_cycle": 1,
        "kline_requests_per_cycle_max": universe,
        "estimated_request_weight_per_cycle": 40 + (2 * universe),
        "documented_request_weight_limit_per_minute": 2400,
        "estimated_request_weight_utilization_pct": round(
            (40 + 2 * universe) * 60 / interval / 2400 * 100, 2,
        ),
        "full_depth_subscriptions": 0,
        "estimated_kline_requests_per_minute": round(universe * 60 / interval, 2),
        "bounded": True,
    }
