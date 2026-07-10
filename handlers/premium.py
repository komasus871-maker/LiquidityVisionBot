from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, PreCheckoutQuery, LabeledPrice

from database.database import add_user
from services.premium import PremiumService, PREMIUM_STARS, PREMIUM_DAYS, CRYPTO_PAYMENT_TEXT

router = Router()
service = PremiumService()
PAYLOAD = f"liquidity_vision_premium_{PREMIUM_DAYS}d"


async def premium_screen(message: Message):
    add_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    status = service.status(message.from_user.id)
    if status["active"]:
        await message.answer(f"""
👑 <b>Liquidity Vision Premium</b>

Статус: ✅ Активен
Тариф: <b>{status['tier']}</b>
До: <code>{status['until']}</code>

Включено:
• расширенная история и статистика;
• smart lifecycle-уведомления;
• больше одновременно отслеживаемых сетапов;
• будущие probability и AI review-модули.
""", parse_mode="HTML")
        return
    await message.answer(
        f"""👑 <b>Liquidity Vision Premium</b>

Current plan: <b>FREE</b>

<b>FREE</b>
• до 20 отслеживаемых сетапов;
• базовая история и Journal;
• Scanner, Market, Fear и News;
• базовый Explain.

<b>PREMIUM — {PREMIUM_STARS} ⭐ / {PREMIUM_DAYS} дней</b>
• до 200 отслеживаемых сетапов;
• полная история;
• Explain Pro;
• расширенная статистика;
• приоритетные lifecycle-уведомления;
• будущие Probability, Similarity и Export-модули.
""", parse_mode="HTML")
    await message.answer_invoice(
        title="Liquidity Vision Premium",
        description=f"Premium на {PREMIUM_DAYS} дней: расширенная статистика, уведомления и PRO-модули.",
        payload=PAYLOAD, currency="XTR",
        prices=[LabeledPrice(label=f"Premium {PREMIUM_DAYS} дней", amount=PREMIUM_STARS)], provider_token="",
    )
    await message.answer(f"💳 <b>Оплата криптовалютой</b>\n\n{CRYPTO_PAYMENT_TEXT}", parse_mode="HTML")


@router.message(Command("premium"))
async def premium_command(message: Message):
    await premium_screen(message)


@router.message(F.text == "👑 Premium")
async def premium_button(message: Message):
    await premium_screen(message)


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    ok = query.invoice_payload == PAYLOAD and query.currency == "XTR" and query.total_amount == PREMIUM_STARS
    await query.answer(ok=ok, error_message=None if ok else "Некорректный платёжный запрос")


@router.message(F.successful_payment)
async def successful_payment(message: Message):
    payment = message.successful_payment
    if payment.invoice_payload != PAYLOAD or payment.currency != "XTR":
        return
    service.record_payment(message.from_user.id, payment)
    until = service.grant(message.from_user.id)
    await message.answer(f"✅ <b>Premium активирован</b>\n\nДоступ действует до: <code>{until}</code>", parse_mode="HTML")
