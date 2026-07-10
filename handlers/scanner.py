from aiogram import Router, F
from aiogram.types import Message

from services.scanner import Scanner

router = Router()

scanner = Scanner()


@router.message(F.text == "🔥 Scanner")
async def scanner_menu(message: Message):

    wait = await message.answer(

        "🔍 Сканирую рынок..."

    )

    results = await scanner.scan()

    text = "🏆 ТОП СЕТАПЫ\n\n"

    for index, coin in enumerate(results[:10], start=1):
        text += (
            f"{index}. 🪙 <b>{coin['symbol']}</b> — "
            f"{coin['recommendation']}\n"
            f"   Score: {coin['confidence']}/100 | RR: 1:{coin['rr']} | Rank: {coin['ranking_score']}\n\n"
        )

    await wait.edit_text(text)