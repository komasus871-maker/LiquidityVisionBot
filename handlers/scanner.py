from aiogram import Router, F
from aiogram.types import Message
from services.scanner import Scanner

router = Router()
scanner = Scanner()


def _section(title, items, limit=5):
    if not items:
        return f"{title}\nНет выраженных возможностей.\n\n"
    text = f"{title}\n\n"
    for index, coin in enumerate(items[:limit], 1):
        text += (
            f"{index}. 🪙 <b>{coin['symbol']}</b> — {coin['recommendation']}\n"
            f"   {coin['market_bias']} | {coin['execution_status']}\n"
            f"   Score: {coin['confidence']}/100 | RR: 1:{coin['rr']} | Edge: {coin['edge']:+.1f}\n"
            f"   Risk: {coin['risk']}\n\n"
        )
    return text


@router.message(F.text == "🔥 Scanner")
async def scanner_menu(message: Message):
    wait = await message.answer("🔍 Сканирую LONG и SHORT сценарии...")
    results = await scanner.scan()
    longs = [x for x in results if x["direction"] == "LONG"]
    shorts = [x for x in results if x["direction"] == "SHORT"]
    balanced = [x for x in results if abs(x["edge"]) < 5]
    text = "🏆 <b>LIQUIDITY VISION SCANNER</b>\n\n"
    text += _section("🟢 <b>Лучшие LONG-возможности</b>", longs)
    text += _section("🔴 <b>Лучшие SHORT-возможности</b>", shorts)
    text += _section("⚖️ <b>Двусторонние рынки</b>", balanced, 3)
    await wait.edit_text(text[:4090], parse_mode="HTML")
