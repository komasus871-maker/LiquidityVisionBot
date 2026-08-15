from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from database.database import add_user
from services.localization import LocalizationService, SUPPORTED_LANGUAGES

router = Router()
i18n = LocalizationService()


@router.message(Command("language"))
async def language(message: Message) -> None:
    add_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    parts = (message.text or "").split(maxsplit=1)
    current = i18n.language(message.from_user.id)
    if len(parts) == 1:
        options = " · ".join(f"<code>{code}</code> {name}" for code, name in SUPPORTED_LANGUAGES.items())
        await message.answer(f"<b>{i18n.t('language.title', language=current)}</b>\n\n{options}\n\n"
                             f"{i18n.t('language.choose', language=current)}")
        return
    normalized = i18n.normalize_language(parts[1])
    if normalized is None:
        await message.answer(i18n.t("language.unsupported", language=current))
        return
    i18n.set_language(message.from_user.id, normalized)
    await message.answer(i18n.t("language.updated", language=normalized,
                                name=SUPPORTED_LANGUAGES[normalized]))
