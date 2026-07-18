from __future__ import annotations

import html
import os

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from services.runtime_diagnostics import collect_runtime_diagnostics

router = Router()


def _admin_ids() -> set[int]:
    values = os.getenv("ADMIN_IDS", os.getenv("ADMIN_ID", ""))
    result: set[int] = set()
    for value in values.replace(";", ",").split(","):
        value = value.strip()
        if value.isdigit():
            result.add(int(value))
    return result


@router.message(Command("admin_status"))
async def admin_status(message: Message) -> None:
    if not message.from_user or message.from_user.id not in _admin_ids():
        await message.answer("⛔ Admin command. Add your Telegram ID to <code>ADMIN_IDS</code> in Render.")
        return

    try:
        report = collect_runtime_diagnostics()
    except Exception as exc:
        await message.answer(f"🔴 Diagnostics failed: <code>{html.escape(str(exc))}</code>")
        return

    icon = {"ok": "🟢", "warning": "🟡", "degraded": "🔴"}.get(report["status"], "⚪")
    counts = report["counts"]
    integrity = report["integrity"]
    worker_lines = []
    for worker in report["workers"]:
        state = "🔴 stale" if worker.get("stale") else "🟢 healthy"
        age = worker.get("age_seconds")
        details = worker.get("details") or {}
        cycle = worker.get("cycle_seconds")
        running = " · running" if worker.get("running") else ""
        last_error = worker.get("last_error")
        line = (f"• <b>{html.escape(str(worker.get('worker_name')))}</b>: {state}{running} · "
                f"age {age if age is not None else '—'}s · cycle {cycle if cycle is not None else '—'}s · "
                f"processed {worker.get('processed_count') or 0} · errors {worker.get('error_count') or 0}")
        if last_error:
            line += f"<br/><code>{html.escape(str(last_error))[:180]}</code>"
        worker_lines.append(line)
    if not worker_lines:
        worker_lines.append("• No worker heartbeat records yet")

    watch_error_lines = []
    for item in report.get("watch_errors", []):
        error = html.escape(str(item.get("last_error") or "unknown error"))
        if len(error) > 180:
            error = error[:177] + "..."
        watch_error_lines.append(
            f"• <b>{html.escape(str(item.get('symbol')))} · {html.escape(str(item.get('timeframe')))}</b> "
            f"· errors {item.get('consecutive_errors') or 0}<br/><code>{error}</code>"
        )
    if not watch_error_lines:
        watch_error_lines.append("• none")

    await message.answer(
        "\n".join([
            f"{icon} <b>Liquidity Vision · Admin Status</b>",
            "",
            f"Version: <code>{html.escape(str(report['version']))}</code>",
            f"Status: <b>{html.escape(str(report['status']).upper())}</b>",
            f"Database: <b>{html.escape(str(report['database_backend']))}</b> · "
            f"{'persistent' if report['persistent_database'] else 'local'} · "
            f"{report['database'].get('latency_ms', '—')} ms",
            f"Uptime: {report['uptime_seconds']}s",
            "",
            "📊 <b>Global runtime counts</b>",
            f"Users: {counts['users']} · Watchlist: {counts['watchlist_items']}",
            f"Observations: {counts['observations']} · Open plans: {counts['open_signals']}",
            f"Global active trades: {counts['active_trades']} · Global closed records: {counts['closed_signals']}",
            f"Watch rows with errors: {counts['watch_errors']}",
            "",
            "🧩 <b>Lifecycle integrity</b>",
            f"Duplicate open plans: {integrity['duplicate_open_plans']}",
            f"Invalid active records: {integrity['active_without_activation_or_stop']}",
            "",
            "⚙️ <b>Workers</b>",
            *worker_lines,
            "",
            "🚨 <b>Watch errors</b>",
            *watch_error_lines,
        ]),
        parse_mode="HTML",
    )


@router.message(Command("workers"))
async def workers_status(message: Message) -> None:
    if not message.from_user or message.from_user.id not in _admin_ids():
        await message.answer("⛔ Admin command.")
        return
    report = collect_runtime_diagnostics()
    lines = ["⚙️ <b>WORKER RELIABILITY</b>", ""]
    for worker in report["workers"]:
        state = "🔴 STALE" if worker.get("stale") else "🟢 HEALTHY"
        details = worker.get("details") or {}
        lines.extend([
            f"<b>{html.escape(str(worker.get('worker_name')))}</b> · {state}",
            f"Running: {bool(worker.get('running'))} · heartbeat age: {worker.get('age_seconds')}s",
            f"Last cycle: {worker.get('cycle_seconds')}s · processed/errors: {worker.get('processed_count') or 0}/{worker.get('error_count') or 0}",
            f"Details: <code>{html.escape(str(details))[:350]}</code>",
            f"Last error: <code>{html.escape(str(worker.get('last_error') or 'none'))[:350]}</code>", ""
        ])
    await message.answer("\n".join(lines), parse_mode="HTML")
