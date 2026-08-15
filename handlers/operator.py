from __future__ import annotations

import json
from html import escape

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from database.database import add_user, connect
from services.capabilities import CapabilityService, PLAN_DEFINITIONS
from services.operator_authorization import (
    OWNER_TELEGRAM_ID,
    OperatorAuthorizationService,
    OperatorCapability,
)
from services.product_analytics import ProductAnalyticsService
from services.runtime_diagnostics import collect_runtime_diagnostics

router = Router()
operators = OperatorAuthorizationService()
entitlements = CapabilityService()
analytics = ProductAnalyticsService()


async def _authorized(message: Message, capability: OperatorCapability, action: str,
                      target: int | None = None) -> bool:
    actor = message.from_user.id if message.from_user else None
    if operators.authorize(actor_telegram_id=actor, capability=capability, action=action,
                           target_telegram_id=target):
        return True
    await message.answer("Operator authorization required. The denied attempt was audited.")
    return False


def _target(value: str, actor: int) -> int | None:
    if value.lower() in {"self", "me"}:
        return actor
    return int(value) if value.isdigit() and int(value) > 0 else None


@router.message(Command("admin_plan", "grant_plan"))
async def admin_plan(message: Message) -> None:
    actor = message.from_user.id if message.from_user else 0
    parts = (message.text or "").split()
    target = _target(parts[1], actor) if len(parts) > 1 else None
    if not await _authorized(message, OperatorCapability.PLAN_ADMIN, "PLAN_GRANT", target):
        return
    if len(parts) not in {3, 4} or target is None:
        await message.answer("Usage: <code>/admin_plan USER_ID PLAN [DAYS]</code>\n"
                             "Example: <code>/admin_plan 7975010097 ELITE 30</code>")
        return
    plan = parts[2].upper()
    if plan not in PLAN_DEFINITIONS or plan == "FREE":
        await message.answer("PLAN must be PRO or ELITE.")
        return
    if len(parts) == 4 and (not parts[3].isdigit() or not 1 <= int(parts[3]) <= 3650):
        await message.answer("DAYS must be an integer from 1 to 3650.")
        return
    days = int(parts[3]) if len(parts) == 4 else None
    add_user(target, None, None)
    previous = entitlements.plan(target)
    source = "OWNER_OVERRIDE" if actor == OWNER_TELEGRAM_ID and target == actor else "ADMIN_GRANT"
    try:
        result = entitlements.assign_plan(target, plan, source=source,
                                          actor_telegram_id=actor, duration_days=days,
                                          audit_metadata={"command": "admin_plan"})
    except (TypeError, ValueError) as exc:
        operators.audit(actor_telegram_id=actor, target_telegram_id=target,
                        capability=OperatorCapability.PLAN_ADMIN, action="PLAN_GRANT",
                        outcome="REJECTED", previous_state=previous,
                        metadata={"error": str(exc)[:200]})
        await message.answer("Plan request rejected. Check the plan and duration.")
        return
    operators.audit(actor_telegram_id=actor, target_telegram_id=target,
                    capability=OperatorCapability.PLAN_ADMIN, action="PLAN_GRANT",
                    outcome="SUCCEEDED", previous_state=previous, new_state=result,
                    metadata={"days": days, "source": source})
    await message.answer(f"Plan <b>{result['plan']}</b> granted to <code>{target}</code>.\n"
                         f"Source: <code>{source}</code> · expiry: <code>{escape(str(result.get('expires_at') or 'none'))}</code>\n"
                         "Trading authority remains unchanged.")


@router.message(Command("admin_plan_revoke", "revoke_plan"))
async def admin_plan_revoke(message: Message) -> None:
    actor = message.from_user.id if message.from_user else 0
    parts = (message.text or "").split()
    target = _target(parts[1], actor) if len(parts) == 2 else None
    if not await _authorized(message, OperatorCapability.PLAN_ADMIN, "PLAN_REVOKE", target):
        return
    if target is None:
        await message.answer("Usage: <code>/admin_plan_revoke USER_ID</code>")
        return
    previous = entitlements.plan(target)
    result = entitlements.revoke_plan(target, source="ADMIN_GRANT",
                                      actor_telegram_id=actor)
    operators.audit(actor_telegram_id=actor, target_telegram_id=target,
                    capability=OperatorCapability.PLAN_ADMIN, action="PLAN_REVOKE",
                    outcome="SUCCEEDED", previous_state=previous, new_state=result)
    await message.answer(f"Plan for <code>{target}</code> reset to <b>FREE</b>. Trading authority was not changed.")


@router.message(Command("admin_plan_extend"))
async def admin_plan_extend(message: Message) -> None:
    actor = message.from_user.id if message.from_user else 0
    parts = (message.text or "").split()
    target = _target(parts[1], actor) if len(parts) > 1 else None
    if not await _authorized(message, OperatorCapability.PLAN_ADMIN, "PLAN_EXTEND", target):
        return
    if len(parts) != 3 or target is None or not parts[2].isdigit():
        await message.answer("Usage: <code>/admin_plan_extend USER_ID DAYS</code>")
        return
    previous = entitlements.plan(target)
    try:
        result = entitlements.extend_plan(target, int(parts[2]), source="ADMIN_GRANT",
                                          actor_telegram_id=actor,
                                          audit_metadata={"command": "admin_plan_extend"})
    except ValueError as exc:
        await message.answer(f"Request rejected: <code>{escape(str(exc))}</code>")
        return
    operators.audit(actor_telegram_id=actor, target_telegram_id=target,
                    capability=OperatorCapability.PLAN_ADMIN, action="PLAN_EXTEND",
                    outcome="SUCCEEDED", previous_state=previous, new_state=result)
    await message.answer(f"Plan extended. New expiry: <code>{escape(str(result.get('expires_at')))}</code>")


@router.message(Command("admin_plan_status"))
async def admin_plan_status(message: Message) -> None:
    actor = message.from_user.id if message.from_user else 0
    parts = (message.text or "").split()
    target = _target(parts[1], actor) if len(parts) == 2 else None
    if not await _authorized(message, OperatorCapability.PLAN_ADMIN, "PLAN_STATUS", target):
        return
    if target is None:
        await message.answer("Usage: <code>/admin_plan_status USER_ID</code>")
        return
    plan = entitlements.plan(target)
    await message.answer(f"<b>Plan Status · {target}</b>\n\nPlan: <b>{plan['plan']}</b>\n"
                         f"Source: <code>{escape(str(plan['source']))}</code>\n"
                         f"Expiry: <code>{escape(str(plan.get('expires_at') or 'none'))}</code>\n"
                         "Economic authority: <code>FALSE</code>")


@router.message(Command("admin_entitlements"))
async def admin_entitlements(message: Message) -> None:
    actor = message.from_user.id if message.from_user else 0
    parts = (message.text or "").split()
    target = _target(parts[1], actor) if len(parts) == 2 else None
    if not await _authorized(message, OperatorCapability.PLAN_ADMIN, "ENTITLEMENT_STATUS", target):
        return
    if target is None:
        await message.answer("Usage: <code>/admin_entitlements USER_ID</code>")
        return
    snapshot = entitlements.snapshot(target)
    enabled = [name for name, item in snapshot.items() if item["enabled"]]
    disabled = [name for name, item in snapshot.items() if not item["enabled"]]
    await message.answer(f"<b>Entitlements · {target}</b>\n\nEnabled ({len(enabled)}):\n"
                         f"<code>{escape(', '.join(enabled))}</code>\n\nDisabled ({len(disabled)}):\n"
                         f"<code>{escape(', '.join(disabled))}</code>\n\nExecution authority: <code>FALSE</code>")


@router.message(Command("admin_users", "admin_plans"))
async def admin_users(message: Message) -> None:
    if not await _authorized(message, OperatorCapability.SYSTEM_ADMIN, "USER_AGGREGATES"):
        return
    report = analytics.usage(30)
    await message.answer("<b>Admin · Users and Plans</b>\n\n"
                         f"Registered users: <b>{report['registered_users']}</b>\n"
                         f"Active users (30d): <b>{report['active_users']}</b>\n"
                         f"Plans: <code>{escape(json.dumps(report['plans'], sort_keys=True))}</code>\n\n"
                         "No usernames, messages, credentials, or exchange data are exposed.")


@router.message(Command("admin_usage"))
async def admin_usage(message: Message) -> None:
    if not await _authorized(message, OperatorCapability.SYSTEM_ADMIN, "PRODUCT_USAGE"):
        return
    report = analytics.usage(7)
    commands = ", ".join(f"{row['capability'].replace('COMMAND:', '/')}: {row['n']}"
                         for row in report["commands"]) or "none"
    await message.answer("<b>Admin · Product Usage (7d)</b>\n\n"
                         f"Active users: <b>{report['active_users']}</b>\n"
                         f"Top commands: <code>{escape(commands)}</code>\n"
                         f"Outcomes: <code>{escape(json.dumps(report['outcomes'], default=str))}</code>\n"
                         f"Alerts: <code>{escape(json.dumps(report['alerts'], default=str))}</code>")


@router.message(Command("admin_ai_usage"))
async def admin_ai_usage(message: Message) -> None:
    if not await _authorized(message, OperatorCapability.AI_ADMIN, "AI_USAGE"):
        return
    today, week = analytics.ai_usage(1), analytics.ai_usage(7)
    await message.answer("<b>Admin · AI Usage</b>\n\n"
                         f"Today: <code>{escape(json.dumps(today['summary'], default=str))}</code>\n"
                         f"7 days: <code>{escape(json.dumps(week['summary'], default=str))}</code>\n"
                         f"Providers/models: <code>{escape(json.dumps(week['providers'], default=str))}</code>\n\n"
                         "Top users are available only as numeric Telegram IDs; no prompts are exposed.")


@router.message(Command("admin", "admin_health", "admin_worker_status"))
async def admin_dashboard(message: Message) -> None:
    if not await _authorized(message, OperatorCapability.SYSTEM_ADMIN, "ADMIN_DASHBOARD"):
        return
    health = collect_runtime_diagnostics()
    usage = analytics.usage(1)
    counts = health["counts"]
    await message.answer("<b>Liquidity Vision · Admin Dashboard</b>\n\n"
                         f"Service / DB: <code>{health['status'].upper()} / {health['database_backend'].upper()}</code>\n"
                         f"Users / active today: <b>{usage['registered_users']} / {usage['active_users']}</b>\n"
                         f"Plans: <code>{escape(json.dumps(usage['plans'], sort_keys=True))}</code>\n"
                         f"AI decisions / invalid: <b>{counts['ai_decisions']} / {counts['ai_invalid_responses']}</b>\n"
                         f"Copy queue retry/dead: <b>{counts['execution_retry_wait']} / {counts['execution_dead_letter']}</b>\n"
                         f"LIVE accounts read-only/certified/enabled/suspended: <b>{counts['live_read_only_accounts']} / {counts['live_certified_accounts']} / {counts['live_enabled_accounts']} / {counts['live_suspended_accounts']}</b>\n"
                         f"LIVE queue planned/claimed/recovery: <b>{counts['live_queue_planned']} / {counts['live_queue_claimed']} / {counts['live_queue_recovery']}</b>\n"
                         f"LIVE reconciliation/PnL failures/kill switches: <b>{counts['live_reconciliation_unresolved']} / {counts['live_daily_pnl_failures']} / {counts['live_active_kill_switches']}</b>\n"
                         f"Alerts today: <code>{escape(json.dumps(usage['alerts'], default=str))}</code>\n"
                         f"Workers stale: <code>{escape(', '.join(health['stale_workers']) or 'none')}</code>\n\n"
                         "Use /admin_usage, /admin_ai_usage, /admin_users, or /admin_plan_status USER_ID.")
