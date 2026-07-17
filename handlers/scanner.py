from aiogram import Router, F
from aiogram.types import Message
from services.scanner import Scanner
from utils.price import fmt_price
from utils.presentation import category_label, reason_label

router = Router()
scanner = Scanner()


def _ranked(results: list[dict], limit: int = 8) -> str:
    if not results:
        return "Выраженных возможностей не найдено."
    seen: set[str] = set()
    lines: list[str] = []
    for coin in results:
        symbol = str(coin.get("symbol") or "").upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        rank = len(lines) + 1
        category = category_label(coin.get("category"))
        lines.append(
            f"{rank}. <b>{symbol}</b> · {coin.get('direction', 'NEUTRAL')} · {category}\n"
            f"   Исполнение <b>{float(coin.get('ranking_score') or 0):.1f}</b>/100 · Направление {float(coin.get('confidence') or 0):.0f} · Готовность {float(coin.get('readiness') or 0):.0f}\n"
            f"   Качество входа {float(coin.get('entry_quality') or 0):.0f}/100 · Риск: {reason_label(coin.get('risk'))}"
        )
        if coin.get("category") == "PULLBACK":
            lines[-1] += f"\n   Zone {fmt_price(coin.get('preferred_entry_low'))} – {fmt_price(coin.get('preferred_entry_high'))}"
        if len(lines) >= limit:
            break
    return "\n\n".join(lines)


@router.message(F.text == "🔥 Scanner")
async def scanner_menu(message: Message):
    wait = await message.answer("🔍 Сканирую рынок и строю единый рейтинг...")
    results = await scanner.scan()
    long_count = sum(str(x.get("direction")) == "LONG" for x in results)
    short_count = sum(str(x.get("direction")) == "SHORT" for x in results)
    ready_count = sum(str(x.get("category")) == "READY_NOW" for x in results)
    text = (
        "🏆 <b>LIQUIDITY VISION SCANNER 4.1</b>\n\n"
        f"Активов: <b>{len(results)}</b> · Ready Now: <b>{ready_count}</b> · LONG/SHORT: <b>{long_count}/{short_count}</b>\n"
        "Единый рейтинг по реальной готовности к исполнению. Заблокированные сценарии получают штраф.\n\n"
        f"{_ranked(results)}"
    )
    await wait.edit_text(text[:4090], parse_mode="HTML")
