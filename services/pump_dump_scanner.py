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
import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, Iterable, Sequence

from database.database import connect


WINDOW_MINUTES = (1, 3, 5, 15, 30, 60)


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
    )


class PumpDumpScanner:
    version = "pump-dump-scanner-v1"

    @staticmethod
    def _severity(move: float, threshold: float, normalized: float | None,
                  relative_volume: float | None) -> Severity:
        ratio = abs(move) / max(threshold, 0.01)
        if ratio >= 2.5 or (ratio >= 1.8 and (normalized or 0) >= 4 and (relative_volume or 0) >= 3):
            return Severity.EXTREME
        if ratio >= 1.5 or ((normalized or 0) >= 3 and (relative_volume or 0) >= 2):
            return Severity.STRONG
        return Severity.NORMAL

    def detect(self, snapshot: SymbolSnapshot, settings: ScannerSettings) -> list[dict[str, Any]]:
        if not settings.enabled or snapshot.symbol in settings.muted_symbols:
            return []
        if (snapshot.quote_volume_24h is not None
                and snapshot.quote_volume_24h < settings.minimum_quote_volume_24h):
            return []
        ranks = {Severity.NORMAL: 0, Severity.STRONG: 1, Severity.EXTREME: 2}
        events: list[dict[str, Any]] = []
        for window in settings.windows:
            if window not in snapshot.changes_pct:
                continue
            move = snapshot.changes_pct[window]
            if abs(move) < settings.move_threshold_pct:
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
            events.append({
                "symbol": snapshot.symbol, "venue": snapshot.venue,
                "direction": direction, "severity": severity,
                "window_minutes": window, "move_pct": move,
                "price_start": snapshot.start_prices[window], "price_end": snapshot.price,
                "relative_volume": snapshot.relative_volume,
                "volatility_normalized_move": snapshot.normalized_moves.get(window),
                "snapshot": snapshot, "observed_at": snapshot.observed_at,
                "classification": "MARKET_ALERT", "economic_authority": False,
                "version": self.version,
            })
        return events


class ScannerRepository:
    """Persistent settings, episodes and bounded alert history."""

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
        )

    @staticmethod
    def save_settings(telegram_id: int, settings: ScannerSettings) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with connect() as conn:
            conn.execute("""INSERT INTO scanner_user_settings(
                telegram_id,enabled,market_scope,custom_symbols_json,windows_json,
                move_threshold_pct,minimum_quote_volume_24h,minimum_severity,
                cooldown_seconds,re_alert_pct,pump_alerts,dump_alerts,liquidation_alerts,
                oi_shock_alerts,funding_extreme_alerts,muted_symbols_json,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                muted_symbols_json=excluded.muted_symbols_json,updated_at=excluded.updated_at""",
                (telegram_id, int(settings.enabled), settings.market_scope,
                 json.dumps(settings.custom_symbols), json.dumps(settings.windows),
                 settings.move_threshold_pct, settings.minimum_quote_volume_24h,
                 settings.minimum_severity.value, settings.cooldown_seconds, settings.re_alert_pct,
                 int(settings.pump_alerts), int(settings.dump_alerts),
                 int(settings.liquidation_alerts), int(settings.oi_shock_alerts),
                 int(settings.funding_extreme_alerts), json.dumps(settings.muted_symbols), now))

    @staticmethod
    def subscribers() -> list[tuple[int, ScannerSettings]]:
        with connect() as conn:
            rows = conn.execute("SELECT telegram_id FROM scanner_user_settings WHERE enabled=1").fetchall()
        return [(int(row[0]), ScannerRepository.settings(int(row[0]))) for row in rows]

    @staticmethod
    def recent(limit: int = 50, *, telegram_id: int | None = None) -> list[dict[str, Any]]:
        where = " WHERE telegram_id=?" if telegram_id is not None else ""
        params: tuple[Any, ...] = ((telegram_id, max(1, min(200, int(limit))))
                                   if telegram_id is not None else (max(1, min(200, int(limit))),))
        with connect() as conn:
            rows = conn.execute(f"""SELECT * FROM market_anomaly_events{where}
                ORDER BY observed_at DESC LIMIT ?""", params).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def stats_24h(symbol: str | None = None, *, telegram_id: int | None = None,
                  now: datetime | None = None) -> dict[str, Any]:
        current = now or datetime.now(timezone.utc)
        cutoff = (current - timedelta(hours=24)).isoformat()
        sql = "SELECT direction,move_pct,observed_at FROM market_anomaly_events WHERE observed_at>=?"
        params: tuple[Any, ...] = (cutoff,)
        if telegram_id is not None:
            sql += " AND telegram_id=?"
            params += (telegram_id,)
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
        return result.rowcount > 0

    @staticmethod
    def close_inactive(*, older_than: datetime) -> int:
        with connect() as conn:
            result = conn.execute(
                "UPDATE scanner_episode_state SET active=0 WHERE active=1 AND last_seen_at<?",
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
                alert = escalation or extension or elapsed >= settings.cooldown_seconds
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
                signal_count_24h,snapshot_json,classification,economic_authority,observed_at,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(event_id) DO NOTHING""",
                (event_id, telegram_id, episode_id, event["symbol"], event["venue"], "PUMP_DUMP",
                 event["direction"], event["severity"].value, event["window_minutes"],
                 event["move_pct"], event["price_start"], event["price_end"],
                 event["relative_volume"], event["volatility_normalized_move"], signal_count,
                 json.dumps(snapshot.public_dict(), sort_keys=True), "MARKET_ALERT", 0,
                 now.isoformat(), datetime.now(timezone.utc).isoformat()))
        return MarketAlert(
            event_id=event_id, episode_id=episode_id, symbol=event["symbol"],
            venue=event["venue"], direction=event["direction"], severity=event["severity"],
            window_minutes=event["window_minutes"], move_pct=event["move_pct"],
            price_start=event["price_start"], price_end=event["price_end"],
            relative_volume=event["relative_volume"],
            volatility_normalized_move=event["volatility_normalized_move"],
            signal_count_24h=signal_count, observed_at=now, snapshot=snapshot,
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
    ]
    return tuple({"alert_type": kind, "classification": "MARKET_ALERT",
                  "economic_authority": False, **details}
                 for kind, active, details in candidates if active)


def render_alert_card(alert: MarketAlert) -> str:
    snapshot = alert.snapshot
    icon = "🟢" if alert.direction == "PUMP" else "🔴"
    lines = [
        f"{icon} <b>MARKET ALERT · {alert.direction} · {alert.symbol}</b>",
        f"<b>{alert.move_pct:+.2f}% / {alert.window_minutes}m</b>",
        f"<code>{alert.price_start:.8g} → {alert.price_end:.8g}</code>", "",
        f"Severity: <b>{alert.severity.value}</b>",
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
    ]
    lines.extend(f"{label}: {value}" for label, value in optional if value is not None)
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
        "full_depth_subscriptions": 0,
        "estimated_kline_requests_per_minute": round(universe * 60 / interval, 2),
        "bounded": True,
    }
