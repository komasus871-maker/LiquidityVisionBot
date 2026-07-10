from aiogram import Router, F
from aiogram.types import Message
from services.scanner import Scanner

router = Router()
scanner = Scanner()


@router.message(F.text == "🔥 Scanner")
async def scanner_menu(message: Message):
    wait = await message.answer("🔍 Сканирую рынок...")
    results = await scanner.scan()
    if not results:
        await wait.edit_text("Сейчас нет выраженного рыночного преимущества. Рынок остаётся нейтральным.")
        return

    text = "🏆 <b>ТОП РЫНОЧНЫЕ ВОЗМОЖНОСТИ</b>\n\n"
    for index, coin in enumerate(results[:10], start=1):
        text += (
            f"{index}. 🪙 <b>{coin['symbol']}</b> — {coin['recommendation']}\n"
            f"   {coin['market_bias']} | {coin['execution_status']}\n"
            f"   Score: {coin['confidence']}/100 | RR: 1:{coin['rr']} | Confirmations: {coin['confirmations']}\n"
            f"   Risk: {coin['risk']}\n\n"
        )
    await wait.edit_text(text, parse_mode="HTML")
