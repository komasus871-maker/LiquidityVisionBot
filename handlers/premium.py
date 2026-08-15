from __future__ import annotations

import os
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import LabeledPrice, Message, PreCheckoutQuery

from database.database import add_user
from services.capabilities import CapabilityService, PLAN_DEFINITIONS, PLAN_VERSION
from services.premium import PREMIUM_DAYS, PREMIUM_STARS, PremiumService


router = Router()
service = PremiumService()
entitlements = CapabilityService()
PAYLOAD = f"liquidity_vision_pro_{PREMIUM_DAYS}d_v1"


def _admin_ids() -> set[int]:
    raw = os.getenv("ADMIN_IDS", os.getenv("ADMIN_ID", ""))
    return {int(value.strip()) for value in raw.replace(";", ",").split(",")
            if value.strip().isdigit()}


def _plan_lines(plan: str) -> str:
    definitions = {
        "FREE": ["Core signals and lifecycle", "Basic journal and PAPER copy",
                 "Useful market-intelligence previews"],
        "PRO": ["Full Quality V3, Market Story and Ranking V4", "Order book, funding and OI",
                "Advanced alerts and copy customization"],
        "ELITE": ["Strategy Lab and Edge Discovery", "Forward/scalping/portfolio research",
                  "Advanced AI red-team comparison and exports"],
    }
    return "\n".join(f"• {escape(item)}" for item in definitions[plan])


async def premium_screen(message: Message) -> None:
    add_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    current = entitlements.plan(message.from_user.id)
    await message.answer(
        "<b>Liquidity Vision Intelligence · Plans</b>\n\n"
        f"Current plan: <b>{current['plan']}</b>\n"
        f"Expires: <code>{escape(str(current.get('expires_at') or 'no expiry'))}</code>\n\n"
        f"<b>FREE</b>\n{_plan_lines('FREE')}\n\n"
        f"<b>PRO · {PREMIUM_STARS} Telegram Stars / {PREMIUM_DAYS} days</b>\n{_plan_lines('PRO')}\n\n"
        f"<b>ELITE INTELLIGENCE</b>\n{_plan_lines('ELITE')}\n\n"
        "Plans provide additional depth, personalization and research—not profitability or trading authority.\n"
        f"Plan definitions: <code>{PLAN_VERSION}</code>", parse_mode="HTML")
    if current["plan"] == "FREE":
        await message.answer_invoice(
            title="Liquidity Vision Intelligence · PRO",
            description=f"PRO market-intelligence access for {PREMIUM_DAYS} days.",
            payload=PAYLOAD, currency="XTR",
            prices=[LabeledPrice(label=f"PRO · {PREMIUM_DAYS} days", amount=PREMIUM_STARS)],
            provider_token="",
        )


@router.message(Command("premium", "plans"))
async def premium_command(message: Message) -> None:
    await premium_screen(message)


@router.message(Command("my_plan"))
async def my_plan(message: Message) -> None:
    add_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    current = entitlements.plan(message.from_user.id)
    enabled = [name for name, value in entitlements.snapshot(message.from_user.id).items()
               if value["enabled"]]
    await message.answer(
        f"<b>My Plan · {current['plan']}</b>\n\n"
        f"Source: <code>{escape(str(current['source']))}</code>\n"
        f"Expires: <code>{escape(str(current.get('expires_at') or 'no expiry'))}</code>\n"
        f"Enabled capabilities: <b>{len(enabled)}</b>\n"
        f"Usage limits: <code>{escape(str(entitlements.limits(message.from_user.id)))}</code>\n\n"
        "No plan can bypass copy safety, risk limits, AI governance or LIVE gates.",
        parse_mode="HTML")


@router.message(Command("grant_plan"))
async def grant_plan(message: Message) -> None:
    if not message.from_user or message.from_user.id not in _admin_ids():
        await message.answer("Operator command.")
        return
    parts = (message.text or "").split()
    if len(parts) < 3 or not parts[1].isdigit():
        await message.answer("Usage: <code>/grant_plan USER_ID PLAN [DAYS]</code>\n"
                             "Example: <code>/grant_plan 123456 PRO 30</code>", parse_mode="HTML")
        return
    user_id, plan = int(parts[1]), parts[2].upper()
    days = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else None
    try:
        add_user(user_id, None, None)
        result = entitlements.assign_plan(user_id, plan, source="OPERATOR_MANUAL",
                                          actor_telegram_id=message.from_user.id,
                                          duration_days=days)
    except ValueError as exc:
        await message.answer(f"Invalid request: <code>{escape(str(exc))}</code>", parse_mode="HTML")
        return
    await message.answer(f"Plan <b>{result['plan']}</b> granted to <code>{user_id}</code>.",
                         parse_mode="HTML")


@router.message(Command("revoke_plan"))
async def revoke_plan(message: Message) -> None:
    if not message.from_user or message.from_user.id not in _admin_ids():
        await message.answer("Operator command.")
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Usage: <code>/revoke_plan USER_ID</code>\n"
                             "Example: <code>/revoke_plan 123456</code>", parse_mode="HTML")
        return
    result = entitlements.revoke_plan(int(parts[1]), source="OPERATOR_MANUAL",
                                      actor_telegram_id=message.from_user.id)
    await message.answer(f"Plan reset to <b>{result['plan']}</b>.", parse_mode="HTML")


@router.message(F.text == "👑 Premium")
async def premium_button(message: Message) -> None:
    await premium_screen(message)


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery) -> None:
    valid = query.invoice_payload == PAYLOAD and query.currency == "XTR" and query.total_amount == PREMIUM_STARS
    await query.answer(ok=valid, error_message=None if valid else "Invalid payment request")


@router.message(F.successful_payment)
async def successful_payment(message: Message) -> None:
    payment = message.successful_payment
    if payment.invoice_payload != PAYLOAD or payment.currency != "XTR":
        return
    add_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    if not service.record_payment(message.from_user.id, payment):
        await message.answer("This Telegram payment has already been processed.")
        return
    until = service.grant(message.from_user.id, tier="PRO")
    await message.answer(f"✅ <b>PRO activated</b>\nAccess expires: <code>{escape(until)}</code>",
                         parse_mode="HTML")
