from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from services.command_catalog import HELP_CATALOG, OPERATOR_HELP, category_text
from services.localization import LocalizationService

router = Router()
i18n = LocalizationService()


@router.message(Command("help"))
async def help_command(message: Message):
    parts = (message.text or "").split(maxsplit=1)
    language = i18n.language(message.from_user.id if message.from_user else None)
    category = parts[1].strip().lower() if len(parts) > 1 else ""
    aliases = {"premium": "account", "plans": "account", "settings": "account", "scanner": "market"}
    category = aliases.get(category, category)
    if category == "operator":
        await message.answer(f"<b>Liquidity Vision · Operator Reference</b>\n\n{OPERATOR_HELP}")
        return
    if category:
        detail = category_text(category, language=language)
        if detail is None:
            await message.answer(i18n.t("help.unknown", language=language))
            return
        await message.answer(detail)
        return

    lines = [
        f"<b>{i18n.t('help.title', language=language)}</b>", "",
        i18n.t("help.intro", language=language), "",
    ]
    descriptions = {
        "market": "analysis, story, quality, liquidity and public market data",
        "trading": "watchlist, journal, replay and PAPER positions",
        "copy": "PAPER copy policy, queue, fills and diagnostics",
        "intelligence": "rankings, contradictions and advisory AI observations",
        "research": "strategy, edge, cohorts and forward validation",
        "system": "system and data health",
        "account": "profile, preferences, exchanges and plans",
    }
    for name in HELP_CATALOG:
        purpose = i18n.t(f"help.{name}", language=language)
        lines.append(f"/<b>help {name}</b> — {purpose if purpose != i18n.t('common.unavailable', language=language) else descriptions[name]}")
    lines += ["", "Start here: <code>/analyze BTC 1h</code> · <code>/scanner</code>",
              i18n.t("help.disclaimer", language=language)]
    await message.answer("\n".join(lines))
