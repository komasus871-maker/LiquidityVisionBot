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
        worker_lines.append(
            f"• <b>{html.escape(str(worker.get('worker_name')))}</b>: {state} · "
            f"age {age if age is not None else '—'}s · errors {worker.get('error_count') or 0}"
        )
    if not worker_lines:
        worker_lines.append("• No worker heartbeat records yet")

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
            "📊 <b>Runtime counts</b>",
            f"Users: {counts['users']} · Watchlist: {counts['watchlist_items']}",
            f"Observations: {counts['observations']} · Open plans: {counts['open_signals']}",
            f"Active trades: {counts['active_trades']} · Closed: {counts['closed_signals']}",
            f"Watch rows with errors: {counts['watch_errors']}",
            "",
            "🧩 <b>Lifecycle integrity</b>",
            f"Duplicate open plans: {integrity['duplicate_open_plans']}",
            f"Invalid active records: {integrity['active_without_activation_or_stop']}",
            "",
            "⚙️ <b>Workers</b>",
            *worker_lines,
        ]),
        parse_mode="HTML",
    )
