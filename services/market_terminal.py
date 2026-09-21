"""Compact Telegram presentation for shared forward market state."""
from __future__ import annotations

import html
import os
from datetime import datetime, timezone
from typing import Any


def _number(value: Any, digits: int = 2, suffix: str = "") -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):,.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return "—"


def _age_seconds(value: str | None) -> int | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return max(0, int((datetime.now(timezone.utc) - parsed).total_seconds()))
    except (TypeError, ValueError):
        return None


def render_market_overview(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return (
            "📊 <b>MARKET TERMINAL</b>\n\n"
            "Forward current-state is not available yet. Baseline analysis remains available via "
            "<code>/analyze BTC 1h</code>."
        )
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_symbol.setdefault(str(row["symbol"]), []).append(row)
    cards = []
    for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        values = by_symbol.get(symbol, [])
        if not values:
            cards.append(f"<b>{symbol}</b> · no current forward state")
            continue
        freshest = max(values, key=lambda item: item.get("observed_at") or "")
        snapshot = freshest["snapshot"]
        book = snapshot.get("book") or {}
        flow = (snapshot.get("trade_flow") or {}).get("horizons_ms") or {}
        derivatives = snapshot.get("derivatives") or {}
        cross = snapshot.get("cross_venue") or {}
        age = _age_seconds(freshest.get("observed_at"))
        cards.append(
            f"<b>{symbol.replace('USDT', '/USDT')}</b> · {html.escape(str(snapshot.get('market_state') or 'UNKNOWN'))}\n"
            f"Price <b>{_number(book.get('mid'), 2)}</b> · spread {_number(book.get('spread_bps'), 2, ' bps')}\n"
            f"CVD 1m {_number((flow.get('60000') or {}).get('normalized_delta'), 3)} · "
            f"book {_number(book.get('depth_imbalance'), 3)}\n"
            f"OI Δ {_number(derivatives.get('open_interest_change'), 4)} · funding "
            f"{_number(derivatives.get('funding_rate'), 6)} · basis {_number(derivatives.get('basis_bps'), 2, ' bps')}\n"
            f"Cross-venue {_number(cross.get('dispersion_bps'), 2, ' bps')} · "
            f"quality <code>{html.escape(str(freshest.get('data_quality') or 'UNKNOWN'))}</code> · "
            f"age {age if age is not None else '—'}s"
        )
    return (
        "📊 <b>MARKET TERMINAL</b>\n"
        "<i>Public derivatives intelligence · not a trade signal</i>\n\n" +
        "\n\n".join(cards) +
        "\n\nRefresh: <code>/market_now</code> · Analysis: <code>/analyze BTC 1h</code>"
    )


def render_order_flow(rows: list[dict[str, Any]], symbol: str = "BTCUSDT") -> str:
    selected = [row for row in rows if row.get("symbol") == symbol]
    if not selected:
        return f"🌊 <b>ORDER FLOW · {html.escape(symbol)}</b>\n\nNo current collector state."
    lines = []
    for row in selected:
        snapshot = row["snapshot"]
        book = snapshot.get("book") or {}
        flow = (snapshot.get("trade_flow") or {})
        horizons = flow.get("horizons_ms") or {}
        derivatives = snapshot.get("derivatives") or {}
        liquidation = (snapshot.get("liquidations") or {}).get("60000") or {}
        lines.append(
            f"<b>{html.escape(str(row['venue']))}</b> · <code>{html.escape(str(row.get('data_quality') or 'UNKNOWN'))}</code>\n"
            f"spread {_number(book.get('spread_bps'), 2, ' bps')} · imbalance {_number(book.get('depth_imbalance'), 3)} · "
            f"microprice {_number(book.get('microprice'), 2)}\n"
            f"CVD 1m {_number((horizons.get('60000') or {}).get('normalized_delta'), 3)} · "
            f"CVD 5m {_number((horizons.get('300000') or {}).get('normalized_delta'), 3)}\n"
            f"OI {_number(derivatives.get('open_interest'), 2)} · funding {_number(derivatives.get('funding_rate'), 6)} · "
            f"basis {_number(derivatives.get('basis_bps'), 2, ' bps')}\n"
            f"liq buy/sell ${_number(liquidation.get('forced_buy_notional'), 0)} / "
            f"${_number(liquidation.get('forced_sell_notional'), 0)}"
        )
    return (
        f"🌊 <b>ORDER FLOW · {html.escape(symbol)}</b>\n"
        "<i>Market intelligence only · no automatic trade inference</i>\n\n" +
        "\n\n".join(lines) + "\n\nUse <code>/orderflow ETHUSDT</code> or <code>/orderflow SOLUSDT</code>."
    )


def render_shadow_status(health: dict[str, Any] | None) -> str:
    if not health:
        state, age, gaps, last_event = "NOT STARTED", None, 0, "—"
    else:
        age = _age_seconds(health.get("heartbeat_at"))
        threshold = max(30, int(os.getenv("FORWARD_HEARTBEAT_STALE_SECONDS", "180")))
        state = "STALE" if age is None or age > threshold else str(health.get("state") or "UNKNOWN")
        gaps = int(health.get("gap_count") or 0)
        last_event = html.escape(str(health.get("last_event_at") or "—"))
    return (
        "🧪 <b>FORWARD SHADOW LAB</b>\n\n"
        f"Collector: <b>{html.escape(state)}</b>\n"
        f"Heartbeat age: <b>{age if age is not None else '—'}s</b>\n"
        f"Last public event: <code>{last_event}</code>\n"
        f"Recorded discontinuities: <b>{gaps}</b>\n"
        "Candidates: <b>10 frozen · SHADOW ONLY</b>\n"
        "Execution authority: <b>NONE</b>\n\n"
        "Evidence is accumulating. Candidate WR, PF, expectancy and PnL remain hidden until the preregistered review gate."
    )


def render_system_status(
    health: dict[str, Any] | None, operational: dict[str, Any] | None = None,
    scanner: dict[str, Any] | None = None,
) -> str:
    live = os.getenv("LIVE_EXECUTION_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    collector = render_shadow_status(health).splitlines()[2] if health else "Collector: <b>NOT STARTED</b>"
    operational_age = _age_seconds((operational or {}).get("heartbeat_at"))
    operational_threshold = max(
        30, int(os.getenv("OPERATIONAL_HEARTBEAT_STALE_SECONDS", "180")),
    )
    operational_state = str((operational or {}).get("state") or "NOT STARTED")
    if operational and (operational_age is None or operational_age > operational_threshold):
        operational_state = "STALE"
    venue_lines = []
    for name, value in sorted((health or {}).get("venues", {}).items()):
        venue_state = value.get("state") or ("DEGRADED" if value.get("last_error") else "UNKNOWN")
        venue_lines.append(
            f"• {html.escape(name)}: {html.escape(str(venue_state))} · "
            f"{html.escape(str(value.get('reason') or value.get('last_error') or 'awaiting detail'))}"
        )
    scanner = scanner or {}
    scanner_line = (
        f"Scanner: <b>{html.escape(str(scanner.get('scanner_status') or 'NOT STARTED'))}</b> · "
        f"universe {scanner.get('monitored_symbols') if scanner.get('monitored_symbols') is not None else 'unavailable'}"
        f"/{scanner.get('universe_target') or '—'} · baseline {scanner.get('baseline_ready_symbols') or 0} · "
        f"radar age {scanner.get('broad_radar_age_seconds') if scanner.get('broad_radar_age_seconds') is not None else '—'}s"
    )
    storage = (health or {}).get("storage") or {}
    disk_line = (
        f"Disk: <b>{html.escape(str(storage.get('disk_status') or 'UNAVAILABLE'))}</b> · "
        f"{_number(storage.get('usage_percent'), 1, '%')} used · "
        f"{_number(storage.get('free_gb'), 1, ' GiB')} free · "
        f"{_number(storage.get('estimated_days_remaining'), 1, ' days')} remaining"
    )
    object_line = (
        f"Object archive: <b>{html.escape(str(storage.get('object_storage_status') or 'UNAVAILABLE'))}</b> · "
        f"{int(storage.get('pending_partitions') or 0)} pending / "
        f"{_number((storage.get('pending_upload_bytes') or 0) / (1024 ** 3), 2, ' GiB')} · "
        f"{_number(storage.get('estimated_spool_hours_remaining'), 1, 'h')} spool remaining"
    )
    integrity_line = (
        f"Archive integrity: {int(storage.get('checksum_failures') or 0)} checksum failures · "
        f"{int(storage.get('missing_remote_objects') or 0)} missing remote · "
        f"{int(storage.get('manifest_inconsistencies') or 0)} manifest issues"
    )
    return (
        "🩺 <b>SYSTEM</b>\n\n"
        f"Telegram: <b>ONLINE</b>\n{collector}\n"
        f"Product worker: <b>{html.escape(operational_state)}</b> · "
        f"heartbeat {operational_age if operational_age is not None else '—'}s\n"
        f"{scanner_line}\n"
        f"LIVE: <b>{'ENABLED' if live else 'DISABLED'}</b>\n"
        "PAPER: <b>AVAILABLE</b>\n"
        "SHADOW: <b>READ-ONLY / ZERO AUTHORITY</b>\n"
        f"{disk_line}\n{object_line}\n{integrity_line}\n\n"
        "<b>Public feeds</b>\n" + ("\n".join(venue_lines) or "• Awaiting worker heartbeat")
    )
