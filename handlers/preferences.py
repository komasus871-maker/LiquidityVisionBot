from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from database.database import add_user
from services.user_preferences import UserPreferenceService

router = Router()
preferences = UserPreferenceService()


def _render(item: dict) -> str:
    alerts = ", ".join(item["notification_categories"]) or "OFF"
    return ("<b>Personal Settings</b>\n\n"
            f"Output: <code>{item['output_mode']}</code>\n"
            f"Alert verbosity: <code>{item['alert_verbosity']}</code>\n"
            f"Language: <code>{item['language']}</code>\n"
            f"Risk display: <code>{item['risk_presentation']}</code>\n"
            f"Alerts: <code>{alerts}</code>\n\n"
            "Examples: <code>/settings mode detailed</code> · <code>/settings language en</code>\n"
            "<code>/alerts on</code> · <code>/alerts off</code>\n"
            "Preferences cannot override global risk or execution safety.")


@router.message(Command("settings"))
async def settings(message: Message):
    add_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    parts = (message.text or "").split()
    if len(parts) == 1:
        await message.answer(_render(preferences.get(message.from_user.id)))
        return
    if len(parts) != 3:
        await message.answer("Usage: <code>/settings mode compact|detailed</code>\nExample: <code>/settings mode detailed</code>")
        return
    key, value = parts[1].lower(), parts[2].lower()
    if key == "mode" and value in {"compact", "detailed"}:
        result = preferences.update(message.from_user.id, output_mode=value.upper())
    elif key == "language" and value in {"en"}:
        result = preferences.update(message.from_user.id, language=value)
    elif key == "risk" and value in {"r", "percent"}:
        result = preferences.update(message.from_user.id, risk_presentation=value.upper())
    else:
        await message.answer("Unsupported setting. Example: <code>/settings mode detailed</code>")
        return
    await message.answer(_render(result))


@router.message(Command("alerts"))
async def alerts(message: Message):
    add_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    parts = (message.text or "").split()
    if len(parts) == 1:
        await message.answer(_render(preferences.get(message.from_user.id)))
        return
    value = parts[1].lower()
    if value not in {"on", "off", "compact", "detailed"}:
        await message.answer("Usage: <code>/alerts on|off|compact|detailed</code>\nExample: <code>/alerts on</code>")
        return
    if value in {"on", "off"}:
        result = preferences.update(message.from_user.id,
                                    notification_categories=["MATERIAL_INTELLIGENCE", "TRADE_LIFECYCLE"] if value == "on" else [])
    else:
        result = preferences.update(message.from_user.id, alert_verbosity=value.upper())
    await message.answer(_render(result))
