from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import LabeledPrice, Message, PreCheckoutQuery

from database.database import add_user
from services.capabilities import CapabilityService, PLAN_DEFINITIONS, PLAN_VERSION
from services.premium import PREMIUM_DAYS, PREMIUM_STARS, PremiumService
from services.localization import LocalizationService


router = Router()
service = PremiumService()
entitlements = CapabilityService()
PAYLOAD = f"liquidity_vision_pro_{PREMIUM_DAYS}d_v1"
i18n = LocalizationService()


def _plan_lines(plan: str) -> str:
    definitions = {
        "FREE": ["Core signals and lifecycle", "Basic journal and PAPER copy",
                 "Useful market-intelligence previews"],
        "PRO": ["Full Quality V4, Market Story and Ranking V5", "Order book, funding and OI",
                "Advanced alerts and copy customization"],
        "ELITE": ["Strategy Lab and Edge Discovery", "Forward/scalping/portfolio research",
                  "Advanced AI red-team comparison and exports"],
    }
    return "\n".join(f"• {escape(item)}" for item in definitions[plan])


async def premium_screen(message: Message) -> None:
    add_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    current = entitlements.plan(message.from_user.id)
    language = i18n.language(message.from_user.id)
    await message.answer(
        f"<b>{i18n.t('plans.title', language=language)}</b>\n\n"
        f"{i18n.t('plans.current', language=language, plan=current['plan'])}\n"
        f"{i18n.t('plans.expires', language=language, expiry=escape(str(current.get('expires_at') or 'no expiry')))}\n\n"
        f"<b>FREE</b>\n{_plan_lines('FREE')}\n\n"
        f"<b>PRO · {PREMIUM_STARS} Telegram Stars / {PREMIUM_DAYS} days</b>\n{_plan_lines('PRO')}\n\n"
        f"<b>ELITE INTELLIGENCE</b>\n{_plan_lines('ELITE')}\n\n"
        f"{i18n.t('plans.disclaimer', language=language)}\n"
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
    language = i18n.language(message.from_user.id)
    enabled = [name for name, value in entitlements.snapshot(message.from_user.id).items()
               if value["enabled"]]
    await message.answer(
        f"<b>{i18n.t('my_plan.title', language=language, plan=current['plan'])}</b>\n\n"
        f"{i18n.t('my_plan.source', language=language, source=escape(str(current['source'])))}\n"
        f"Expires: <code>{escape(str(current.get('expires_at') or 'no expiry'))}</code>\n"
        f"{i18n.t('my_plan.enabled', language=language, count=len(enabled))}\n"
        f"{i18n.t('my_plan.limits', language=language, limits=escape(str(entitlements.limits(message.from_user.id))))}\n\n"
        "No plan can bypass copy safety, risk limits, AI governance or LIVE gates.",
        parse_mode="HTML")


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
