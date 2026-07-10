from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from database.database import add_user
from keyboards.main_menu import main_keyboard

router = Router()


@router.message(Command("start"))
async def start(message: Message):

    add_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )

    text = f"""
🚀 <b>Liquidity Vision</b>

Добро пожаловать,
<b>{message.from_user.first_name}</b>

━━━━━━━━━━━━━━━━━━━━

Профессиональный терминал
для крипто-трейдеров.

Выберите нужный раздел 👇
"""

    await message.answer(
        text,
        reply_markup=main_keyboard()
    )