from aiogram import Router, F
from aiogram.types import Message
from services.scanner import Scanner
from utils.price import fmt_price

router = Router(); scanner = Scanner()


def _section(title, items, limit=5):
    if not items:
        return f"{title}\nНет выраженных возможностей.\n\n"
    text = f"{title}\n\n"
    for index, coin in enumerate(items[:limit], 1):
        text += (
            f"{index}. 🪙 <b>{coin['symbol']}</b> — {coin['recommendation']}\n"
            f"   {coin['market_bias']} | {coin['execution_status']}\n"
            f"   Direction: {coin['confidence']}/100 | Entry: {coin['entry_quality']}/100 | Ready: {coin['readiness']}/100\n"
            f"   RR: 1:{coin['rr']} | Edge: {coin['edge']:+.1f}\n"
        )
        if coin['category'] == 'PULLBACK':
            text += f"   Zone: {fmt_price(coin['preferred_entry_low'])} - {fmt_price(coin['preferred_entry_high'])}\n"
        text += f"   Risk: {coin['risk']}\n\n"
    return text


@router.message(F.text == "🔥 Scanner")
async def scanner_menu(message: Message):
    wait = await message.answer("🔍 Сканирую рынок по режимам исполнения...")
    results = await scanner.scan()
    ready = [x for x in results if x['category'] == 'READY_NOW']
    pullback = [x for x in results if x['category'] == 'PULLBACK']
    confirmation = [x for x in results if x['category'] == 'CONFIRMATION']
    reversal = [x for x in results if x['category'] == 'REVERSAL']
    shorts = [x for x in results if x['direction'] == 'SHORT']
    text = "🏆 <b>LIQUIDITY VISION SCANNER 2.0</b>\n\n"
    text += _section("🚀 <b>Ready Now</b>", ready)
    text += _section("🎯 <b>Pullback Opportunities</b>", pullback)
    text += _section("🔔 <b>Confirmation Watch</b>", confirmation)
    text += _section("🔄 <b>Reversal Watch</b>", reversal, 3)
    text += _section("🔴 <b>Best SHORT Scenarios</b>", shorts, 5)
    await wait.edit_text(text[:4090], parse_mode="HTML")
