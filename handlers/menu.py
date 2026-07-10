from aiogram import Router
from aiogram.types import CallbackQuery

router = Router()


@router.callback_query()
async def callbacks(callback: CallbackQuery):

    data = callback.data

    if data == "market":

        await callback.message.edit_text(
            "📈 Раздел Market находится в разработке."
        )

    elif data == "analyze":

        await callback.message.edit_text(
            "📊 Скоро здесь появится AI-анализ."
        )

    elif data == "scanner":

        await callback.message.edit_text(
            "🔥 Scanner пока недоступен."
        )

    elif data == "journal":

        await callback.message.edit_text(
            "💰 Journal скоро будет добавлен."
        )

    elif data == "news":

        await callback.message.edit_text(
            "📰 Новости скоро появятся."
        )

    elif data == "profile":

        await callback.message.edit_text(
            f"""
👤 {callback.from_user.first_name}

ID

<code>{callback.from_user.id}</code>
"""
        )

    elif data == "premium":

        await callback.message.edit_text(
            """
⭐ Premium

Пока закрыто.
"""
        )

    await callback.answer()