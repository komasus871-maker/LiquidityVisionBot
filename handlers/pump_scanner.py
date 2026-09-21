from __future__ import annotations

import json
from dataclasses import replace
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from services.pump_dump_scanner import (
    DISCOVERY_MODES, SCANNER_ALERT_TYPES, ScannerRepository, Severity, resource_budget,
)


router = Router()
repository = ScannerRepository()

SCANNER_ENTRY_LABELS = frozenset({"⚡ Scanner", "⚡ Pump / Dump"})
SCANNER_VIEW_LABELS = {
    "HOT_NOW": "🔥 Hot Now",
    "EARLY_BUILDUP": "🌱 Early Buildup",
    "NEW_ANOMALIES": "⚡ New Anomalies",
    "STRONGEST_FLOW": "📈 Strongest Flow",
    "OI_BUILDUP": "🏗 OI Buildup",
    "OI_SHOCK": "💥 OI Shock",
    "SQUEEZES": "⚡ All Squeezes",
    "SHORT_SQUEEZES": "🧨 Short Squeezes",
    "LONG_SQUEEZES": "🩸 Long Squeezes",
    "LIQUIDATION_CASCADES": "💣 Liquidation Cascades",
    "LIQUIDITY_VACUUM": "🌊 Liquidity Vacuum",
    "CVD_DIVERGENCES": "↔ CVD Divergences",
    "CROSS_VENUE": "🌐 Cross-Venue",
    "EXHAUSTION_WATCH": "⚠ Exhaustion Watch",
    "VOLUME_EXPLOSION": "📊 Volume Explosion",
    "VOLATILITY_COMPRESSION": "🧊 Volatility Compression",
    "BREAKOUT_IGNITION": "🚀 Breakout Ignition",
}


def _scanner_home_keyboard() -> InlineKeyboardMarkup:
    entries = list(SCANNER_VIEW_LABELS.items())
    rows = [[InlineKeyboardButton(text=label, callback_data=f"scanview:{mode}")
             for mode, label in entries[index:index + 2]]
            for index in range(0, len(entries), 2)]
    rows.append([InlineKeyboardButton(text="⚙ Scanner Settings", callback_data="pdsettings")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _scanner_view_keyboard(mode: str, rows: list[dict]) -> InlineKeyboardMarkup:
    keyboard: list[list[InlineKeyboardButton]] = []
    if rows:
        first = rows[0]
        symbol = str(first["symbol"])
        for row in rows[:6]:
            keyboard.append([InlineKeyboardButton(
                text=f"Why {str(row['symbol'])}?",
                callback_data=f"scanwhy:{row['id']}",
            )])
        keyboard.append([
            InlineKeyboardButton(text="🌊 Order Flow", callback_data=f"flow:{symbol}"),
            InlineKeyboardButton(text="⭐ Watch", callback_data=f"watch:{symbol.removesuffix('USDT')}:1h"),
        ])
        keyboard.append([
            InlineKeyboardButton(text="↻ Refresh", callback_data=f"scanview:{mode}"),
        ])
    keyboard.append([InlineKeyboardButton(text="← Scanner Home", callback_data="scanhome")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def _age_text(value: int | float | None) -> str:
    if value is None:
        return "unavailable"
    seconds = max(0, int(value))
    return f"{seconds}s" if seconds < 120 else f"{seconds // 60}m {seconds % 60}s"


def _venue_summary(stats: dict) -> str:
    venues = stats.get("venues") or {}
    if not venues:
        return "unavailable"
    return f"{stats['venues_healthy']} healthy / {stats['venues_degraded']} degraded"


def _scanner_home_text(user_id: int) -> str:
    stats = repository.home_stats(telegram_id=user_id)
    monitored = stats["monitored_symbols"]
    target = stats["universe_target"]
    if monitored is None:
        universe = f"Unavailable · {escape(stats['scanner_status_reason'])}"
    else:
        universe = f"{monitored} / target {target}"
    venue_lines = []
    for name, value in sorted((stats.get("venues") or {}).items()):
        venue_lines.append(
            f"• {escape(str(name))}: <b>{escape(str(value.get('state') or 'UNKNOWN'))}</b> · "
            f"{escape(str(value.get('reason') or 'reason unavailable'))}"
        )
    return (
        "⚡ <b>LIQUIDITY VISION SCANNER</b>\n\n"
        "Real-time anomaly &amp; opportunity intelligence.\n"
        "<i>MARKET ALERT ≠ TRADE SIGNAL ≠ execution authority</i>\n\n"
        f"Scanner status: <b>{escape(stats['scanner_status'])}</b>\n"
        f"Monitored symbols: <b>{universe}</b>\n"
        f"Baseline ready: <b>{stats['baseline_ready_symbols']} / {monitored or target}</b> "
        f"({stats['baseline_required_minutes']}m history)\n"
        f"Estimated readiness: <b>{'~' + str(stats['estimated_readiness_minutes']) + 'm' if stats['estimated_readiness_minutes'] is not None else 'dependent on provider recovery'}</b>\n"
        f"Fetched / failed: <b>{stats.get('successfully_fetched') or 0} / "
        f"{stats.get('failed_symbol_count') or 0}</b>\n"
        f"Shortlisted / enriched: <b>{stats['shortlisted_symbols']} / "
        f"{stats['deep_enrichment_symbols']}</b>\n"
        f"Active episodes: <b>{stats['active_episodes']}</b>\n"
        f"New anomalies 1h: <b>{stats['new_anomalies_1h']}</b>\n"
        f"Escalations 1h: <b>{stats['escalations_1h']}</b>\n"
        f"Scanner heartbeat: <b>{_age_text(stats['scanner_heartbeat_age_seconds'])}</b>\n"
        f"Broad radar: <b>{_age_text(stats['broad_radar_age_seconds'])}</b>\n"
        f"Episode engine: <b>{_age_text(stats['episode_engine_age_seconds'])}</b>\n"
        f"Forward collector: <b>{_age_text(stats['forward_collector_heartbeat_age_seconds'])}</b>\n"
        f"Cycle duration: <b>{stats['cycle_duration_seconds'] if stats['cycle_duration_seconds'] is not None else 'unavailable'}s</b>\n"
        f"Venues: <b>{_venue_summary(stats)}</b>\n"
        + ("\n" + "\n".join(venue_lines) if venue_lines else "")
    )


def _scanner_view_text(user_id: int, mode: str) -> tuple[str, list[dict]]:
    rows = repository.recent(12, telegram_id=user_id, mode=mode)
    severity = {"NORMAL": 0, "STRONG": 1, "EXTREME": 2}
    rows.sort(key=lambda row: (
        severity.get(str(row.get("severity")), 0),
        abs(float(row.get("move_pct") or 0)),
        str(row.get("observed_at") or ""),
    ), reverse=True)
    stats = repository.stats_24h(telegram_id=user_id)
    lines = [f"{escape(SCANNER_VIEW_LABELS[mode])}",
             "<i>Ranked descriptive anomalies · never trade authority</i>", ""]
    if not rows:
        health = repository.home_stats(telegram_id=user_id)
        lines += [
            "No qualifying anomaly episodes are currently available.", "",
            f"Scanner: <b>{escape(health['scanner_status'])}</b>",
            f"Universe: <b>{health['monitored_symbols'] or 0} / target {health['universe_target']}</b>",
            f"Baseline ready: <b>{health['baseline_ready_symbols']} / "
            f"{health['monitored_symbols'] or health['universe_target']}</b>",
            f"Estimated readiness: <b>{'~' + str(health['estimated_readiness_minutes']) + 'm' if health['estimated_readiness_minutes'] is not None else 'provider-dependent'}</b>",
            f"Last radar cycle: <b>{_age_text(health['broad_radar_age_seconds'])} ago</b>",
            f"Shortlisted: <b>{health['shortlisted_symbols']}</b>",
            f"Venues: <b>{_venue_summary(health)}</b>",
        ]
    for row in rows:
        icon = "🟢" if row["direction"] == "PUMP" else "🔴"
        try:
            snapshot = json.loads(row.get("snapshot_json") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            snapshot = {}
        percentile = (snapshot.get("historical_percentile") or {}).get(str(row["window_minutes"]))
        freshness = snapshot.get("freshness_seconds")
        lines.append(
            f"{icon} <b>{escape(str(row['symbol']))}</b> · "
            f"<b>{escape(str(row.get('phase') or 'EARLY_ANOMALY').replace('_', ' '))}</b>\n"
            f"{float(row['move_pct']):+.2f}% / {row['window_minutes']}m · "
            f"{escape(str(row.get('market_state') or 'UNKNOWN_MIXED').replace('_', ' '))}\n"
            f"Severity / evidence: {escape(str(row['severity']))} / "
            f"{escape(str(row.get('evidence_quality') or 'BASIC'))} · "
            f"Abnormality: {f'{float(percentile):.1f}th pct' if percentile is not None else 'unavailable'} · "
            f"Freshness: {f'{float(freshness):.0f}s' if freshness is not None else 'unavailable'}"
        )
    budget = resource_budget()
    lines += ["", f"24h episodes: {stats['signals']} · universe cap: {budget['universe_limit']} · "
                     f"cycle: {budget['scan_interval_seconds']}s"]
    return "\n\n".join(lines), rows


def _settings_text(user_id: int) -> str:
    value = repository.settings(user_id)
    sensitivity = (
        "SENSITIVE" if value.move_threshold_pct < 3
        else "STRICT" if value.move_threshold_pct >= 5 or value.minimum_severity is Severity.STRONG
        else "NORMAL"
    )
    return (
        "⚙ <b>SCANNER SETTINGS</b>\n"
        "<i>MARKET ALERTS · never trade authority</i>\n\n"
        "<b>GENERAL</b>\n"
        f"Scanner: <b>{'ON' if value.enabled else 'OFF'}</b>\n"
        f"Notifications: <b>{'ON' if value.notifications_enabled else 'OFF'}</b>\n"
        f"Quiet mode: <b>{'ON' if value.quiet_mode else 'OFF'}</b>\n\n"
        "<b>UNIVERSE</b>\n"
        f"Scope: <b>{escape(value.market_scope.replace('_', ' '))}</b>\n"
        f"Minimum volume: <b>${value.minimum_quote_volume_24h / 1_000_000:g}M</b>\n\n"
        "<b>SENSITIVITY</b>\n"
        f"Preset: <b>{sensitivity}</b> · move {value.move_threshold_pct:.2f}% · "
        f"severity {value.minimum_severity.value}\n"
        f"Windows: <b>{' · '.join(f'{item}m' for item in value.windows)}</b>\n"
        f"Cooldown: <b>{value.cooldown_seconds // 60}m</b>\n\n"
        "<b>PUMP / DUMP</b>\n"
        f"Pump / Dump: <b>{'ON' if value.pump_alerts else 'OFF'} / "
        f"{'ON' if value.dump_alerts else 'OFF'}</b>\n\n"
        "<b>DERIVATIVES</b>\n"
        f"OI / Funding / Liquidations: <b>{'ON' if value.oi_shock_alerts else 'OFF'} / "
        f"{'ON' if value.funding_extreme_alerts else 'OFF'} / "
        f"{'ON' if value.liquidation_alerts else 'OFF'}</b>\n"
        f"Categories: <b>{len(value.enabled_alert_types)}/{len(SCANNER_ALERT_TYPES)}</b>\n\n"
        "<b>MUTED SYMBOLS</b>\n"
        f"{escape(', '.join(value.muted_symbols) or 'None')}\n\n"
        "Buttons cover normal configuration. Advanced commands remain available via "
        "<code>/help scanner</code>."
    )


def _settings_keyboard(user_id: int) -> InlineKeyboardMarkup:
    value = repository.settings(user_id)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Scanner {'ON' if value.enabled else 'OFF'}", callback_data="scanset:enabled"),
         InlineKeyboardButton(text="Sensitivity", callback_data="scanset:sensitivity")],
        [InlineKeyboardButton(text="Universe", callback_data="scanset:universe"),
         InlineKeyboardButton(text="Windows", callback_data="scanset:windows")],
        [InlineKeyboardButton(text="Volume", callback_data="scanset:volume"),
         InlineKeyboardButton(text="Categories", callback_data="scanset:categories")],
        [InlineKeyboardButton(text=f"Notifications {'ON' if value.notifications_enabled else 'OFF'}",
                              callback_data="scanset:notifications"),
         InlineKeyboardButton(text=f"Quiet {'ON' if value.quiet_mode else 'OFF'}",
                              callback_data="scanset:quiet")],
        [InlineKeyboardButton(text="Pump", callback_data="scanset:pump"),
         InlineKeyboardButton(text="Dump", callback_data="scanset:dump"),
         InlineKeyboardButton(text="Derivatives", callback_data="scanset:derivatives")],
        [InlineKeyboardButton(text="Muted Symbols", callback_data="scanset:muted"),
         InlineKeyboardButton(text="← Back", callback_data="scanhome")],
    ])


@router.message(Command("scanner"))
@router.message(Command("pump_scan"))
@router.message(F.text == "⚡ Pump / Dump")
@router.message(F.text == "⚡ Scanner")
async def pump_scan(message: Message) -> None:
    raw_text = (message.text or "").strip()
    parts = (message.text or "").split(maxsplit=1)
    if raw_text in SCANNER_ENTRY_LABELS or len(parts) == 1:
        await message.answer(
            _scanner_home_text(message.from_user.id), parse_mode="HTML",
            reply_markup=_scanner_home_keyboard(),
        )
        return
    mode = (parts[1] if len(parts) == 2 else "HOT_NOW").upper().replace(" ", "_").replace("-", "_")
    if mode not in DISCOVERY_MODES:
        await message.answer(
            "Unknown view. Use: <code>hot_now</code>, <code>early_buildup</code>, "
            "<code>new_anomalies</code>, <code>strongest_flow</code>, <code>oi_buildup</code>, "
            "<code>squeezes</code>, <code>liquidation_cascades</code>, "
            "<code>liquidity_vacuum</code>, <code>cross_venue</code>, or "
            "<code>exhaustion_watch</code>.", parse_mode="HTML",
        )
        return
    text, rows = _scanner_view_text(message.from_user.id, mode)
    await message.answer(text[:4090], parse_mode="HTML",
                         reply_markup=_scanner_view_keyboard(mode, rows))


@router.callback_query(F.data == "scanhome")
async def scanner_home_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.edit_text(
        _scanner_home_text(callback.from_user.id), parse_mode="HTML",
        reply_markup=_scanner_home_keyboard(),
    )


@router.callback_query(F.data.startswith("scanview:"))
async def scanner_view_callback(callback: CallbackQuery) -> None:
    mode = callback.data.split(":", 1)[1]
    if mode not in SCANNER_VIEW_LABELS:
        await callback.answer("Unknown scanner view", show_alert=True)
        return
    text, rows = _scanner_view_text(callback.from_user.id, mode)
    await callback.answer()
    await callback.message.edit_text(
        text[:4090], parse_mode="HTML", reply_markup=_scanner_view_keyboard(mode, rows),
    )


@router.callback_query(F.data.startswith("scanwhy:"))
async def scanner_why_callback(callback: CallbackQuery) -> None:
    try:
        record_id = int(callback.data.split(":", 1)[1])
    except (TypeError, ValueError):
        await callback.answer("Invalid scanner record", show_alert=True)
        return
    row = repository.event(record_id, telegram_id=callback.from_user.id)
    if not row:
        await callback.answer("Scanner record is no longer available", show_alert=True)
        return
    try:
        reasons = json.loads(row.get("reasons_json") or "[]")
        risks = json.loads(row.get("risk_flags_json") or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        reasons, risks = [], []
    lines = [f"🔎 <b>WHY {escape(str(row['symbol']))} IS RANKED</b>", ""]
    lines.extend(f"✓ {escape(str(reason))}" for reason in reasons)
    if not reasons:
        lines.append("Evidence explanation unavailable for this older record.")
    if risks:
        lines += ["", "<b>Warnings</b>", *[f"⚠ {escape(str(risk))}" for risk in risks]]
    lines += ["", f"Evidence: <b>{escape(str(row.get('evidence_quality') or 'BASIC'))}</b>",
              "Market intelligence only — no execution authority."]
    await callback.answer()
    await callback.message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("scanner_settings"))
async def scanner_settings(message: Message) -> None:
    user_id = message.from_user.id
    current = repository.settings(user_id)
    parts = (message.text or "").split()
    try:
        if len(parts) >= 2:
            action = parts[1].lower()
            if action in {"on", "off"}:
                current = replace(current, enabled=action == "on")
            elif action == "threshold" and len(parts) == 3:
                value = float(parts[2])
                if not 0.5 <= value <= 25:
                    raise ValueError("threshold must be 0.5–25")
                current = replace(current, move_threshold_pct=value)
            elif action == "windows" and len(parts) == 3:
                values = tuple(sorted({int(item) for item in parts[2].split(",")}))
                if not values or not set(values) <= {1, 3, 5, 15, 30, 60}:
                    raise ValueError("unsupported window")
                current = replace(current, windows=values)
            elif action == "severity" and len(parts) == 3:
                current = replace(current, minimum_severity=Severity(parts[2].upper()))
            elif action == "volume" and len(parts) == 3:
                volume = float(parts[2])
                if not 1_000_000 <= volume <= 10_000_000_000:
                    raise ValueError("volume must be 1m–10bn USDT")
                current = replace(current, minimum_quote_volume_24h=volume)
            elif action == "cooldown" and len(parts) == 3:
                minutes = int(parts[2])
                if not 5 <= minutes <= 1440:
                    raise ValueError("cooldown must be 5–1440 minutes")
                current = replace(current, cooldown_seconds=minutes * 60)
            elif action == "scope" and len(parts) >= 3:
                scope = {"all": "ALL_LIQUID", "watchlist": "WATCHLIST_ONLY",
                         "custom": "CUSTOM"}.get(parts[2].lower())
                if not scope:
                    raise ValueError("scope must be all, watchlist, or custom")
                custom = current.custom_symbols
                if scope == "CUSTOM":
                    if len(parts) != 4:
                        raise ValueError("custom scope requires comma-separated symbols")
                    custom = tuple(sorted({
                        item.upper().replace("/", "").replace("-", "")
                        if item.upper().endswith("USDT") else item.upper() + "USDT"
                        for item in parts[3].split(",") if item.strip()
                    }))
                    if not custom or len(custom) > 25:
                        raise ValueError("custom scope requires 1–25 symbols")
                current = replace(current, market_scope=scope, custom_symbols=custom)
            elif action in {"pump", "dump", "oi", "funding", "liquidations"} and len(parts) == 3:
                enabled = {"on": True, "off": False}.get(parts[2].lower())
                if enabled is None:
                    raise ValueError("toggle must be on or off")
                field = {"pump": "pump_alerts", "dump": "dump_alerts", "oi": "oi_shock_alerts",
                         "funding": "funding_extreme_alerts",
                         "liquidations": "liquidation_alerts"}[action]
                current = replace(current, **{field: enabled})
            elif action in {"notifications", "quiet"} and len(parts) == 3:
                enabled = {"on": True, "off": False}.get(parts[2].lower())
                if enabled is None:
                    raise ValueError("toggle must be on or off")
                field = "notifications_enabled" if action == "notifications" else "quiet_mode"
                current = replace(current, **{field: enabled})
            elif action == "alerts" and len(parts) == 3:
                requested = tuple(dict.fromkeys(
                    item.strip().upper() for item in parts[2].split(",") if item.strip()
                ))
                if requested == ("ALL",):
                    requested = SCANNER_ALERT_TYPES
                unknown = set(requested) - set(SCANNER_ALERT_TYPES)
                if not requested or unknown:
                    raise ValueError(
                        "alerts must be all or comma-separated: " + ",".join(SCANNER_ALERT_TYPES)
                    )
                current = replace(current, enabled_alert_types=requested)
            elif action == "mute" and len(parts) == 3:
                symbol = parts[2].upper().replace("/", "").replace("-", "")
                if not symbol.endswith("USDT"):
                    symbol += "USDT"
                current = replace(current, muted_symbols=tuple(sorted(set(current.muted_symbols) | {symbol})))
            elif action == "unmute" and len(parts) == 3:
                symbol = parts[2].upper().replace("/", "").replace("-", "")
                if not symbol.endswith("USDT"):
                    symbol += "USDT"
                current = replace(current, muted_symbols=tuple(item for item in current.muted_symbols if item != symbol))
            else:
                raise ValueError("unknown scanner setting")
            repository.save_settings(user_id, current)
        else:
            repository.save_settings(user_id, current)
    except (TypeError, ValueError) as exc:
        await message.answer(f"Invalid scanner setting: {escape(str(exc))}")
        return
    await message.answer(
        _settings_text(user_id), parse_mode="HTML", reply_markup=_settings_keyboard(user_id),
    )


@router.callback_query(F.data.startswith("pdmute:"))
async def mute_callback(callback: CallbackQuery) -> None:
    symbol = callback.data.split(":", 1)[1]
    current = repository.settings(callback.from_user.id)
    repository.save_settings(
        callback.from_user.id,
        replace(current, muted_symbols=tuple(sorted(set(current.muted_symbols) | {symbol}))),
    )
    await callback.answer(f"{symbol} muted", show_alert=True)


@router.callback_query(F.data == "pdsettings")
async def settings_callback(callback: CallbackQuery) -> None:
    # Persist defaults so notification personalization exists immediately;
    # broad radar operation itself is independent of subscribers.
    current = repository.settings(callback.from_user.id)
    repository.save_settings(callback.from_user.id, current)
    await callback.answer()
    await callback.message.edit_text(
        _settings_text(callback.from_user.id), parse_mode="HTML",
        reply_markup=_settings_keyboard(callback.from_user.id),
    )


@router.callback_query(F.data.startswith("scanset:"))
async def settings_control_callback(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id
    action = callback.data.split(":", 1)[1]
    current = repository.settings(user_id)
    if action == "enabled":
        current = replace(current, enabled=not current.enabled)
    elif action == "notifications":
        current = replace(current, notifications_enabled=not current.notifications_enabled)
    elif action == "quiet":
        current = replace(current, quiet_mode=not current.quiet_mode)
    elif action == "pump":
        current = replace(current, pump_alerts=not current.pump_alerts)
    elif action == "dump":
        current = replace(current, dump_alerts=not current.dump_alerts)
    elif action == "derivatives":
        enabled = not (
            current.oi_shock_alerts and current.funding_extreme_alerts
            and current.liquidation_alerts
        )
        current = replace(
            current, oi_shock_alerts=enabled, funding_extreme_alerts=enabled,
            liquidation_alerts=enabled,
        )
    elif action == "universe":
        current = replace(
            current,
            market_scope=("WATCHLIST_ONLY" if current.market_scope == "ALL_LIQUID" else "ALL_LIQUID"),
        )
    elif action == "sensitivity":
        presets = (
            (3.0, Severity.NORMAL), (5.0, Severity.STRONG), (2.0, Severity.NORMAL),
        )
        key = (current.move_threshold_pct, current.minimum_severity)
        try:
            index = presets.index(key)
        except ValueError:
            index = 0
        threshold, severity = presets[(index + 1) % len(presets)]
        current = replace(current, move_threshold_pct=threshold, minimum_severity=severity)
    elif action == "windows":
        presets = ((3, 5, 15, 60), (1, 3, 5, 15, 60), (5, 15, 60))
        try:
            index = presets.index(tuple(current.windows))
        except ValueError:
            index = 0
        current = replace(current, windows=presets[(index + 1) % len(presets)])
    elif action == "volume":
        presets = (20_000_000.0, 50_000_000.0, 100_000_000.0)
        try:
            index = presets.index(float(current.minimum_quote_volume_24h))
        except ValueError:
            index = 0
        current = replace(current, minimum_quote_volume_24h=presets[(index + 1) % len(presets)])
    elif action == "categories":
        enabled = (("PUMP_DUMP", "EARLY_BUILDUP")
                   if len(current.enabled_alert_types) == len(SCANNER_ALERT_TYPES)
                   else SCANNER_ALERT_TYPES)
        current = replace(current, enabled_alert_types=enabled)
    elif action == "muted":
        await callback.answer(
            "Muted: " + (", ".join(current.muted_symbols) or "none")
            + ". Use Mute on an alert or /scanner_settings unmute SYMBOL.",
            show_alert=True,
        )
        return
    else:
        await callback.answer("Unknown scanner setting", show_alert=True)
        return
    repository.save_settings(user_id, current)
    await callback.answer("Scanner setting updated")
    await callback.message.edit_text(
        _settings_text(user_id), parse_mode="HTML", reply_markup=_settings_keyboard(user_id),
    )


@router.callback_query(F.data.startswith("flow:"))
async def orderflow_callback(callback: CallbackQuery) -> None:
    from services.forward_runtime_state import ForwardRuntimeStateRepository
    from services.market_terminal import render_order_flow
    symbol = callback.data.split(":", 1)[1]
    state = ForwardRuntimeStateRepository()
    await callback.answer()
    await callback.message.answer(render_order_flow(state.latest_states((symbol,)), symbol), parse_mode="HTML")
