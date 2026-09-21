from aiogram import Router
from aiogram.types import CallbackQuery

router = Router()


@router.callback_query()
async def callbacks(callback: CallbackQuery):
    # All currently emitted callback namespaces are handled by routers loaded
    # before this final fallback. Old keyboards can survive in Telegram for
    # months, so acknowledge an unknown/retired action instead of silently
    # swallowing it or showing a fake placeholder screen.
    await callback.answer(
        "This action has expired. Open /help or use the main menu.",
        show_alert=True,
    )
