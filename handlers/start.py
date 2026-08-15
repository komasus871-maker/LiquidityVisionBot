from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from database.database import add_user
from keyboards.main_menu import main_keyboard
from services.localization import LocalizationService

router = Router()
i18n = LocalizationService()


@router.message(Command("start"))
async def start(message: Message):
    add_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    language = i18n.language(message.from_user.id)
    first_name = message.from_user.first_name or "trader"
    text = (f"<b>{i18n.t('start.title', language=language)}</b>\n\n"
            f"{i18n.t('start.welcome', language=language, name=first_name)}\n\n"
            f"{i18n.t('start.commands', language=language)}\n\n"
            f"{i18n.t('start.paper', language=language)}")
    await message.answer(text, reply_markup=main_keyboard())
