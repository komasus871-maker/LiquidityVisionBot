from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from utils.fear_greed import FearGreed

router = Router()

fear = FearGreed()


@router.message(Command("fear"))
async def fear_handler(message: Message):

    value, text = await fear.get()

    await message.answer(

        f"""
😨 Fear & Greed Index

📊 Value

{value}

📈 Market

{text}
"""

    )