from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from database.database import add_user
from services.capabilities import CapabilityService
from services.localization import LocalizationService
from services.usage_policy import UsagePolicyService
from services.user_preferences import UserPreferenceService

router = Router()
preferences = UserPreferenceService()
capabilities = CapabilityService()
usage_policy = UsagePolicyService()
i18n = LocalizationService()

ALERT_CATEGORIES = {
    "quality": "QUALITY", "liquidity": "LIQUIDITY", "trade": "TRADE_LIFECYCLE",
    "microstructure": "MICROSTRUCTURE", "ranking": "RANKING", "market": "MATERIAL_INTELLIGENCE",
}
TIMEFRAMES = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"}


def _render(telegram_id: int, item: dict) -> str:
    language = i18n.language(telegram_id)
    alerts = ", ".join(item["notification_categories"]) or "OFF"
    timeframes = ", ".join(item["preferred_timeframes"]) or "any"
    strategies = ", ".join(item["preferred_strategies"]) or "any"
    return (f"<b>{i18n.t('settings.title', language=language)}</b>\n\n"
            f"{i18n.t('settings.output', language=language, value=item['output_mode'])}\n"
            f"{i18n.t('settings.verbosity', language=language, value=item['alert_verbosity'])}\n"
            f"{i18n.t('settings.language', language=language, value=item['language'])}\n"
            f"{i18n.t('settings.risk', language=language, value=item['risk_presentation'])}\n"
            f"Timeframes: <code>{timeframes}</code>\nStrategies: <code>{strategies}</code>\n"
            f"{i18n.t('settings.alerts', language=language, value=alerts)}\n\n"
            f"{i18n.t('settings.guide', language=language)}\n"
            f"{i18n.t('settings.safety', language=language)}")


@router.message(Command("settings"))
async def settings(message: Message):
    user_id = message.from_user.id
    add_user(user_id, message.from_user.username, message.from_user.first_name)
    parts = (message.text or "").split()
    language = i18n.language(user_id)
    if len(parts) == 1:
        await message.answer(_render(user_id, preferences.get(user_id)))
        return
    key, values = parts[1].lower(), [value.lower() for value in parts[2:]]
    if key == "mode" and len(values) == 1 and values[0] in {"compact", "detailed"}:
        result = preferences.update(user_id, output_mode=values[0].upper())
    elif key == "language" and len(values) == 1 and i18n.normalize_language(values[0]):
        i18n.set_language(user_id, values[0])
        result = preferences.get(user_id)
    elif key == "risk" and len(values) == 1 and values[0] in {"r", "percent"}:
        result = preferences.update(user_id, risk_presentation=values[0].upper())
    elif key == "timeframe" and values and all(value in TIMEFRAMES for value in values):
        if not capabilities.has(user_id, "ADVANCED_SETTINGS"):
            await message.answer(capabilities.preview("ADVANCED_SETTINGS", user_id))
            return
        result = preferences.update(user_id, preferred_timeframes=values[:6])
    elif key == "strategy" and values:
        if not capabilities.has(user_id, "ADVANCED_SETTINGS"):
            await message.answer(capabilities.preview("ADVANCED_SETTINGS", user_id))
            return
        result = preferences.update(user_id, preferred_strategies=[value.upper() for value in values[:8]])
    else:
        await message.answer(f"{i18n.t('common.usage', language=language)}: "
                             "<code>/settings mode compact|detailed</code> · "
                             "<code>/settings language en|ru|uk|he|ar</code> · "
                             "<code>/settings timeframe 15m 1h</code>")
        return
    await message.answer(_render(user_id, result))


@router.message(Command("alerts"))
async def alerts(message: Message):
    user_id = message.from_user.id
    add_user(user_id, message.from_user.username, message.from_user.first_name)
    parts = (message.text or "").split()
    current = preferences.get(user_id)
    language = i18n.language(user_id)
    if len(parts) == 1:
        await message.answer(_render(user_id, current))
        return
    if len(parts) == 2 and parts[1].lower() in {"on", "off", "compact", "detailed"}:
        value = parts[1].lower()
        if value in {"on", "off"}:
            categories = (["MATERIAL_INTELLIGENCE", "QUALITY", "TRADE_LIFECYCLE"]
                          if value == "on" else [])
            result = preferences.update(user_id, notification_categories=categories)
        else:
            result = preferences.update(user_id, alert_verbosity=value.upper())
    elif len(parts) == 3 and parts[1].lower() in ALERT_CATEGORIES and parts[2].lower() in {"on", "off"}:
        category = ALERT_CATEGORIES[parts[1].lower()]
        categories = set(current["notification_categories"])
        if parts[2].lower() == "on":
            categories.add(category)
        else:
            categories.discard(category)
        result = preferences.update(user_id, notification_categories=sorted(categories))
    else:
        await message.answer(f"{i18n.t('common.usage', language=language)}: "
                             "<code>/alerts on|off</code> · "
                             "<code>/alerts quality|liquidity|trade|microstructure on|off</code>")
        return
    await message.answer(f"{i18n.t('alerts.updated', language=language)}\n\n{_render(user_id, result)}")


@router.message(Command("usage"))
async def usage(message: Message):
    user_id = message.from_user.id
    add_user(user_id, message.from_user.username, message.from_user.first_name)
    report = usage_policy.status(user_id)
    language = i18n.language(user_id)
    lines = [f"<b>{i18n.t('usage.title', language=language, plan=report['plan'])}</b>", ""]
    for name, item in report["items"].items():
        lines.append(i18n.t("usage.line", language=language, name=name.title(), **item))
    lines += ["", i18n.t("usage.reset", language=language),
              i18n.t("preview.no_authority", language=language)]
    await message.answer("\n".join(lines))
