from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from services.command_catalog import HELP_CATALOG, OPERATOR_HELP, category_text

router = Router()


@router.message(Command("help"))
async def help_command(message: Message):
    parts = (message.text or "").split(maxsplit=1)
    category = parts[1].strip().lower() if len(parts) > 1 else ""
    aliases = {"premium": "account", "plans": "account"}
    category = aliases.get(category, category)
    if category == "operator":
        await message.answer(f"<b>Liquidity Vision · Operator Reference</b>\n\n{OPERATOR_HELP}")
        return
    if category:
        detail = category_text(category)
        if detail is None:
            await message.answer("Unknown help category. Example: <code>/help market</code>")
            return
        await message.answer(detail)
        return

    lines = [
        "<b>Liquidity Vision Intelligence · Help</b>", "",
        "Evidence-led market intelligence, research, and PAPER decision support.", "",
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
        lines.append(f"/<b>help {name}</b> — {descriptions[name]}")
    lines += ["", "Start here: <code>/analyze BTC 1h</code> · <code>/scanner</code>",
              "PAPER means simulated execution. No analysis or plan guarantees profit."]
    await message.answer("\n".join(lines))
