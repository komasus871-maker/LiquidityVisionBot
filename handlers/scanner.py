from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from services.scanner import Scanner
from services.capabilities import CapabilityService
from services.usage_policy import UsagePolicyService
from services.localization import LocalizationService

router = Router()
scanner = Scanner()
usage = UsagePolicyService()
capabilities = CapabilityService()
i18n = LocalizationService()

FILTERS = {
    "strongest": lambda item: True,
    "quality": lambda item: item["quality"] >= 65,
    "ready": lambda item: item["readiness_state"] in {"READY", "READY_WITH_DATA_GAP"},
    "breakout": lambda item: "BREAKOUT" in item["strategy"],
    "liquidity": lambda item: "LIQUIDITY" in item["strategy"],
    "continuation": lambda item: "CONTINUATION" in item["strategy"],
    "reversal": lambda item: "REVERS" in item["strategy"] or "MEAN_REVERSION" in item["strategy"],
    "scalping": lambda item: "SCALPING" in item["strategy"],
}


def _ranked(results: list[dict], limit: int = 8, *, language: str = "en") -> str:
    if not results:
        return i18n.t("scanner.no_results", language=language)
    lines = []
    for index, coin in enumerate(results[:limit], 1):
        lines.append(
            f"<b>{index}. {i18n.market_token(coin['symbol'], language=language)} "
            f"{i18n.market_token(coin['direction'], language=language)} · {i18n.market_token('1H', language=language)}</b>\n"
            f"   Scanner <b>{coin['scanner_score']:.0f}</b> · Quality <b>{coin['quality']:.0f}</b> "
            f"· Readiness <b>{float(coin['readiness']):.0f}</b> ({escape(coin['readiness_state'])})\n"
            f"   Primary: {escape(coin['primary_strategy'].replace('_', ' ').title())} "
            f"({coin['strategy_fit']:.0f}) · Secondary: {escape(coin['secondary_strategy'].replace('_', ' ').title())} "
            f"· gap {coin['strategy_gap']:.0f}\n"
            f"   + {escape(coin['strongest_advantage'])}\n"
            f"   − {escape(coin['strongest_contradiction'])}"
        )
    return "\n\n".join(lines)


@router.message(Command("scanner"))
@router.message(F.text == "🔥 Scanner")
async def scanner_menu(message: Message):
    parts = (message.text or "").split()
    selected = parts[1].lower() if len(parts) > 1 else "strongest"
    language = i18n.language(message.from_user.id)
    custom_filters: dict[str, str] = {}
    if selected == "custom":
        if not capabilities.has(message.from_user.id, "SCANNER_CUSTOM"):
            await message.answer(capabilities.preview("SCANNER_CUSTOM", message.from_user.id))
            return
        try:
            custom_filters = dict(token.split("=", 1) for token in parts[2:] if "=" in token)
            unknown = set(custom_filters) - {"strategy", "direction", "symbol", "min_quality", "min_readiness"}
            if unknown:
                raise ValueError
            float(custom_filters.get("min_quality", 0))
            float(custom_filters.get("min_readiness", 0))
        except (TypeError, ValueError):
            await message.answer("Usage: <code>/scanner custom strategy=breakout direction=long min_quality=65 min_readiness=60</code>")
            return
    elif selected not in FILTERS:
        await message.answer("Usage: <code>/scanner [strongest|quality|ready|breakout|liquidity|continuation|reversal|scalping]</code>\nExample: <code>/scanner breakout</code>")
        return
    allowance = usage.consume(message.from_user.id, "MARKET_SCANNER", "scanner_daily",
                              metadata={"filter": selected})
    if not allowance["allowed"]:
        await message.answer(f"Daily scanner limit reached for {allowance['plan']} ({allowance['limit']}). It resets at 00:00 UTC.")
        return
    wait = await message.answer("Scanning the bounded market universe…")
    results = await scanner.scan()
    if selected == "custom":
        strategy = custom_filters.get("strategy", "").upper()
        direction = custom_filters.get("direction", "").upper()
        symbol = custom_filters.get("symbol", "").upper()
        minimum_quality = float(custom_filters.get("min_quality", 0))
        minimum_readiness = float(custom_filters.get("min_readiness", 0))
        results = [item for item in results
                   if (not strategy or strategy in item["strategy"])
                   and (not direction or direction == item["direction"])
                   and (not symbol or symbol in item["symbol"])
                   and item["quality"] >= minimum_quality
                   and item["readiness"] >= minimum_readiness]
    else:
        results = [item for item in results if FILTERS[selected](item)]
    strategies: dict[str, int] = {}
    for item in results:
        strategies[item["strategy"]] = strategies.get(item["strategy"], 0) + 1
    distribution = ", ".join(f"{key.replace('_', ' ').title()} {value}" for key, value in
                             sorted(strategies.items(), key=lambda pair: (-pair[1], pair[0]))[:4]) or "none"
    text = (
        f"<b>{i18n.t('scanner.title', language=language)}</b>\n"
        f"{i18n.t('scanner.filter', language=language, filter=selected.upper(), remaining=allowance['remaining'])}\n"
        f"{i18n.t('scanner.distribution', language=language, distribution=escape(distribution))}\n\n"
        f"{_ranked(results, language=language)}\n\n"
        f"{i18n.t('scanner.disclaimer', language=language)}"
    )
    await wait.edit_text(text[:4090])
