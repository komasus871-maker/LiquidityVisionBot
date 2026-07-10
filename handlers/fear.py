from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message
from utils.fear_greed import FearGreed

router = Router()
fear = FearGreed()


def interpretation(value: int) -> str:
    if value <= 20: return "Экстремальный страх: возможны панические продажи, но нужен технический триггер."
    if value <= 40: return "Страх: рынок осторожный, импульсы вниз могут быть резкими."
    if value < 60: return "Нейтральный sentiment: больше значения имеет структура и ликвидность."
    if value < 80: return "Жадность: тренд может продолжаться, но риск перегретых входов растёт."
    return "Экстремальная жадность: повышен риск поздних входов и резких фиксаций."


async def send_fear(message: Message):
    try:
        value, text = await fear.get()
        await message.answer(f"😨 <b>Fear & Greed Index</b>\n\nValue: <b>{value}</b>\nMarket: {text}\n\n🧠 {interpretation(value)}", parse_mode="HTML")
    except Exception as exc:
        await message.answer(f"❌ Не удалось получить индекс: {exc}")


@router.message(Command("fear"))
async def fear_command(message: Message):
    await send_fear(message)


@router.message(F.text == "😨 Fear")
async def fear_button(message: Message):
    await send_fear(message)
