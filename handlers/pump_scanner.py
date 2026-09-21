from __future__ import annotations

from dataclasses import replace
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from services.pump_dump_scanner import (
    DISCOVERY_MODES, SCANNER_ALERT_TYPES, ScannerRepository, Severity, resource_budget,
)


router = Router()
repository = ScannerRepository()


def _settings_text(user_id: int) -> str:
    value = repository.settings(user_id)
    return (
        "⚡ <b>PUMP / DUMP SCANNER</b>\n"
        "<i>MARKET ALERTS · never trade authority</i>\n\n"
        f"State: <b>{'ON' if value.enabled else 'OFF'}</b>\n"
        f"Notifications: <b>{'ON' if value.notifications_enabled else 'OFF'}</b> · "
        f"Quiet mode: <b>{'ON' if value.quiet_mode else 'OFF'}</b>\n"
        f"Markets: <b>{escape(value.market_scope)}</b>\n"
        f"Move threshold: <b>{value.move_threshold_pct:.2f}%</b>\n"
        f"Windows: <b>{', '.join(f'{item}m' for item in value.windows)}</b>\n"
        f"Minimum 24h volume: <b>${value.minimum_quote_volume_24h:,.0f}</b>\n"
        f"Severity: <b>{value.minimum_severity.value}</b>\n"
        f"Cooldown: <b>{value.cooldown_seconds // 60}m</b>\n"
        f"Pump / Dump: <b>{'ON' if value.pump_alerts else 'OFF'} / "
        f"{'ON' if value.dump_alerts else 'OFF'}</b>\n\n"
        f"OI / Funding / Liquidations: <b>{'ON' if value.oi_shock_alerts else 'OFF'} / "
        f"{'ON' if value.funding_extreme_alerts else 'OFF'} / "
        f"{'ON' if value.liquidation_alerts else 'OFF'}</b>\n"
        f"Muted: <b>{escape(', '.join(value.muted_symbols) or 'none')}</b>\n\n"
        f"Alert categories: <b>{len(value.enabled_alert_types)}/{len(SCANNER_ALERT_TYPES)} enabled</b>\n\n"
        "Configure: <code>/scanner_settings on|off</code>\n"
        "<code>/scanner_settings threshold 4</code>\n"
        "<code>/scanner_settings windows 1,3,5,15,60</code>\n"
        "<code>/scanner_settings volume 25000000</code>\n"
        "<code>/scanner_settings severity strong</code>\n"
        "<code>/scanner_settings cooldown 20</code>\n"
        "<code>/scanner_settings scope all|watchlist|custom BTC,ETH</code>\n"
        "<code>/scanner_settings pump|dump|oi|funding|liquidations on|off</code>\n"
        "<code>/scanner_settings notifications|quiet on|off</code>\n"
        "<code>/scanner_settings alerts all|TYPE,TYPE</code>\n"
        "<code>/scanner_settings mute|unmute DOGE</code>"
    )


@router.message(Command("pump_scan"))
@router.message(F.text == "⚡ Pump / Dump")
@router.message(F.text == "⚡ Scanner")
async def pump_scan(message: Message) -> None:
    parts = (message.text or "").split(maxsplit=1)
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
    rows = repository.recent(12, telegram_id=message.from_user.id, mode=mode)
    stats = repository.stats_24h(telegram_id=message.from_user.id)
    lines = [f"⚡ <b>{escape(mode.replace('_', ' '))}</b>", "<i>Market anomalies · not trade signals</i>", ""]
    if not rows:
        lines.append("No anomaly episodes have been recorded yet.")
    for row in rows:
        icon = "🟢" if row["direction"] == "PUMP" else "🔴"
        lines.append(
            f"{icon} <b>{escape(str(row['symbol']))}</b> · {float(row['move_pct']):+.2f}% / "
            f"{row['window_minutes']}m · {escape(str(row.get('phase') or 'EARLY_ANOMALY'))} · "
            f"{escape(str(row.get('market_state') or 'UNKNOWN_MIXED'))} · "
            f"{escape(str(row['severity']))}"
        )
    budget = resource_budget()
    lines += ["", f"24h: {stats['pump_events']} pumps · {stats['dump_events']} dumps · "
                   f"{stats['signals']} total episodes",
              f"Universe cap: {budget['universe_limit']} liquid USDT perpetuals · "
                   f"cycle {budget['scan_interval_seconds']}s",
              "Settings: <code>/scanner_settings</code>"]
    lines.append("Views: <code>/pump_scan early_buildup</code> · <code>/pump_scan squeezes</code> · "
                 "<code>/pump_scan exhaustion_watch</code>")
    await message.answer("\n".join(lines), parse_mode="HTML")


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
    await message.answer(_settings_text(user_id), parse_mode="HTML")


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
    await callback.answer()
    await callback.message.answer(_settings_text(callback.from_user.id), parse_mode="HTML")


@router.callback_query(F.data.startswith("flow:"))
async def orderflow_callback(callback: CallbackQuery) -> None:
    from services.forward_runtime_state import ForwardRuntimeStateRepository
    from services.market_terminal import render_order_flow
    symbol = callback.data.split(":", 1)[1]
    state = ForwardRuntimeStateRepository()
    await callback.answer()
    await callback.message.answer(render_order_flow(state.latest_states((symbol,)), symbol), parse_mode="HTML")
