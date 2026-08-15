from html import escape

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from services.command_catalog import HELP_CATALOG, OPERATOR_HELP, category_text
from services.localization import LocalizationService
from services.operator_authorization import OperatorAuthorizationService, OperatorCapability

router = Router()
i18n = LocalizationService()
operators = OperatorAuthorizationService()


@router.message(Command("help"))
@router.message(Command("commands"))
async def help_command(message: Message):
    parts = (message.text or "").split(maxsplit=2)
    language = i18n.language(message.from_user.id if message.from_user else None)
    invoked = parts[0].lstrip("/").split("@", 1)[0].lower() if parts else "help"
    category = parts[1].strip().lower() if len(parts) > 1 else ""
    query = ""
    if invoked == "commands":
        query = " ".join(parts[1:]).strip().lower()
    elif category == "search":
        query = parts[2].strip().lower() if len(parts) > 2 else ""
    if query or invoked == "commands" or category == "search":
        if not query:
            await message.answer("Usage: <code>/help search orderbook</code>")
            return
        matches = []
        seen = set()
        for name, entries in HELP_CATALOG.items():
            for entry in entries:
                if entry.command in seen:
                    continue
                haystack = f"{entry.command} {entry.summary} {entry.usage or ''}".lower()
                if query in haystack:
                    seen.add(entry.command)
                    matches.append((name, entry))
        lines = [f"<b>Command search · {escape(query)}</b>", ""]
        lines.extend(f"/<b>{entry.command}</b> · {escape(name)} — {escape(entry.summary)}"
                     for name, entry in matches[:20])
        await message.answer("\n".join(lines + ([] if matches else ["No matching public command."])))
        return
    if category in {"admin", "operator"}:
        actor = message.from_user.id if message.from_user else None
        if not operators.authorize(actor_telegram_id=actor, capability=OperatorCapability.OWNER,
                                   action="HELP_ADMIN_VIEW"):
            await message.answer(i18n.t("help.unknown", language=language))
            return
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
        "scanner": "Priority Score V3, filters and ranked signals",
        "watchlist": "smart watchlist state, ranking and editing",
        "alerts": "notification categories and delivery preferences",
        "premium": "Free, Pro and Elite capabilities and limits",
        "settings": "language and personal presentation preferences",
        "ai": "advisory AI observations, identity, cost and history",
        "live": "per-user exchange readiness, risk, reconciliation and explicit LIVE state",
    }
    for name in HELP_CATALOG:
        purpose = i18n.t(f"help.{name}", language=language)
        lines.append(f"/<b>help {name}</b> — {purpose if purpose != i18n.t('common.unavailable', language=language) else descriptions[name]}")
    lines += ["", "Start here: <code>/analyze BTC 1h</code> · <code>/scanner</code>",
              i18n.t("help.disclaimer", language=language)]
    await message.answer("\n".join(lines))
