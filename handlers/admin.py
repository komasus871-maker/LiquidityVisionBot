from __future__ import annotations

import html
import json

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from services.runtime_diagnostics import collect_runtime_diagnostics
from services.historical_execution_migration import HistoricalExecutionMigrationService
from services.operator_authorization import OperatorAuthorizationService, OperatorCapability
from services.localization import LocalizationService

router = Router()
migration_service = HistoricalExecutionMigrationService()
operators = OperatorAuthorizationService()
i18n = LocalizationService()


@router.message(Command("system_health"))
async def system_health(message: Message) -> None:
    language = i18n.language(message.from_user.id if message.from_user else None)
    try:
        report = collect_runtime_diagnostics()
    except Exception:
        await message.answer(f"<b>{i18n.t('system.title', language=language)}</b>\n\n"
                             f"{i18n.t('common.unavailable', language=language)}")
        return
    lines = [f"<b>{i18n.t('system.title', language=language)}</b>", "",
             i18n.t("system.database", language=language,
                    backend=html.escape(str(report['database_backend']).upper()),
                    status="HEALTHY" if report['database'].get('ok') else "DEGRADED")]
    provider = (report.get("market_data") or {}).get("primary_provider") or {}
    lines.append(i18n.t("system.provider", language=language,
                        provider=html.escape(str(provider.get('provider') or 'PUBLIC MARKET DATA')),
                        status=html.escape(str(provider.get('status') or 'UNKNOWN'))))
    for worker in report.get("workers", []):
        state = str(worker.get("health_status") or "UNKNOWN")
        age = worker.get("age_seconds")
        line = f"{html.escape(str(worker.get('worker_name')))}: <code>{html.escape(state)}</code> · age {age if age is not None else '—'}s"
        lines.append(line)
    ai = report.get("ai") or {}
    lines += [i18n.t("system.ai", language=language,
                     status=html.escape(str(ai.get('mode') or 'DISABLED'))),
              "", i18n.t("system.details", language=language)]
    await message.answer("\n".join(lines))


@router.message(Command("admin_status"))
async def admin_status(message: Message) -> None:
    actor = message.from_user.id if message.from_user else None
    if not operators.authorize(actor_telegram_id=actor, capability=OperatorCapability.SYSTEM_ADMIN,
                               action="ADMIN_STATUS_VIEW"):
        await message.answer("⛔ Operator authorization required. The denied attempt was audited.")
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
        state = html.escape(str(worker.get("health_status") or
                                ("DEGRADED" if worker.get("stale") else "HEALTHY")))
        age = worker.get("age_seconds")
        details = worker.get("details") or {}
        cycle = worker.get("cycle_seconds")
        running = " · running" if worker.get("running") else ""
        last_error = worker.get("last_error")
        line = (f"• <b>{html.escape(str(worker.get('worker_name')))}</b>: <code>{state}</code>{running} · "
                f"age {age if age is not None else '—'}s · cycle {cycle if cycle is not None else '—'}s · "
                f"processed {worker.get('processed_count') or 0} · errors {worker.get('error_count') or 0}")
        if worker.get("configuration_reason"):
            line += f"<br/><code>{html.escape(str(worker['configuration_reason']))}</code>"
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
            f"Execution claimed/retry/dead: {counts.get('execution_claimed', 0)}/{counts.get('execution_retry_wait', 0)}/{counts.get('execution_dead_letter', 0)}",
            f"Historical migrated/unresolved: {counts.get('historical_migrated', 0)}/{counts.get('historical_unresolved', 0)}",
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


@router.message(Command("migration_status"))
async def migration_status(message: Message) -> None:
    actor = message.from_user.id if message.from_user else None
    if not operators.authorize(actor_telegram_id=actor, capability=OperatorCapability.SYSTEM_ADMIN,
                               action="MIGRATION_STATUS_VIEW"):
        await message.answer("⛔ Admin command.")
        return
    report = migration_service.latest_report()
    latest = report.get("latest_run") or {}
    classes = report.get("classifications") or {}
    await message.answer(
        "\n".join([
            "🧬 <b>Historical migration</b>",
            f"Latest: <b>{html.escape(str(latest.get('status') or 'NOT_RUN'))}</b>",
            f"Scanned/migrated/skipped: {latest.get('scanned_count', 0)}/{latest.get('migrated_count', 0)}/{latest.get('skipped_count', 0)}",
            f"Unresolved: <b>{latest.get('unresolved_count', 0)}</b>",
            f"Last legacy ID: {latest.get('last_legacy_position_id') or '—'}",
            f"Coverage: <code>{html.escape(json.dumps(classes, sort_keys=True))}</code>",
        ]), parse_mode="HTML",
    )


@router.message(Command("workers"))
async def workers_status(message: Message) -> None:
    actor = message.from_user.id if message.from_user else None
    if not operators.authorize(actor_telegram_id=actor, capability=OperatorCapability.SYSTEM_ADMIN,
                               action="WORKER_STATUS_VIEW"):
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
