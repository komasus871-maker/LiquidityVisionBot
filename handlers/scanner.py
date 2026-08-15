from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from services.scanner import Scanner
from services.usage_policy import UsagePolicyService

router = Router()
scanner = Scanner()
usage = UsagePolicyService()

FILTERS = {
    "strongest": lambda item: True,
    "breakout": lambda item: "BREAKOUT" in item["strategy"],
    "liquidity": lambda item: "LIQUIDITY" in item["strategy"],
    "trend": lambda item: "TREND" in item["strategy"],
    "reversal": lambda item: "REVERS" in item["strategy"] or "MEAN_REVERSION" in item["strategy"],
    "scalping": lambda item: "SCALPING" in item["strategy"],
    "high_quality": lambda item: item["quality"] >= 65,
}


def _ranked(results: list[dict], limit: int = 8) -> str:
    if not results:
        return "No opportunity currently meets this evidence filter."
    lines = []
    for index, coin in enumerate(results[:limit], 1):
        lines.append(
            f"<b>{index}. {escape(coin['symbol'])} {escape(coin['direction'])} · 1H</b>\n"
            f"   {escape(coin['strategy'].replace('_', ' ').title())} · Quality <b>{coin['quality']:.0f}</b> "
            f"· readiness <b>{float(coin['readiness']):.0f}</b>\n"
            f"   + {escape(coin['strongest_advantage'])}\n"
            f"   − {escape(coin['strongest_contradiction'])}"
        )
    return "\n\n".join(lines)


@router.message(Command("scanner"))
@router.message(F.text == "🔥 Scanner")
async def scanner_menu(message: Message):
    parts = (message.text or "").split()
    selected = parts[1].lower() if len(parts) > 1 else "strongest"
    if selected not in FILTERS:
        await message.answer("Usage: <code>/scanner [strongest|breakout|liquidity|trend|reversal|scalping|high_quality]</code>\nExample: <code>/scanner breakout</code>")
        return
    allowance = usage.consume(message.from_user.id, "MARKET_SCANNER", "scanner_daily",
                              metadata={"filter": selected})
    if not allowance["allowed"]:
        await message.answer(f"Daily scanner limit reached for {allowance['plan']} ({allowance['limit']}). It resets at 00:00 UTC.")
        return
    wait = await message.answer("Scanning the bounded market universe…")
    results = [item for item in await scanner.scan() if FILTERS[selected](item)]
    strategies: dict[str, int] = {}
    for item in results:
        strategies[item["strategy"]] = strategies.get(item["strategy"], 0) + 1
    distribution = ", ".join(f"{key.replace('_', ' ').title()} {value}" for key, value in
                             sorted(strategies.items(), key=lambda pair: (-pair[1], pair[0]))[:4]) or "none"
    text = (
        f"<b>Liquidity Vision · Opportunity Feed</b>\n"
        f"Filter: <code>{selected.upper()}</code> · remaining today: <b>{allowance['remaining']}</b>\n"
        f"Strategy distribution: {escape(distribution)}\n\n{_ranked(results)}\n\n"
        "Analytical ranking only. Scores are evidence diagnostics, not probabilities or execution instructions."
    )
    await wait.edit_text(text[:4090])
