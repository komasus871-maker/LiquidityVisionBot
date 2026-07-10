from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from services.binance import get_price

router = Router()


@router.message(Command("price"))
async def price(message: Message):

    command = message.text.split()

    if len(command) != 2:
        await message.answer("Использование:\n/price BTC")
        return

    symbol = command[1]

    data = await get_price(symbol)

    if "priceChangePercent" not in data:
        await message.answer("Монета не найдена.")
        return

    text = f"""
📈 {data["symbol"]}

💲 Цена: {data["lastPrice"]}

📊 24ч: {data["priceChangePercent"]}%

⬆ High: {data["highPrice"]}

⬇ Low: {data["lowPrice"]}

📦 Объем: {data["quoteVolume"]}
"""

    await message.answer(text)