from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from services.market import Market
from services.symbol_resolver import SymbolResolver

router = Router()
market = Market()
resolver = SymbolResolver(market)


def _fmt(value: float) -> str:
    if abs(value) >= 1000:
        return f"{value:,.2f}"
    if abs(value) >= 1:
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return f"{value:.12f}".rstrip("0").rstrip(".")


@router.message(Command("price"))
async def price(message: Message):
    command = message.text.split()
    if len(command) != 2:
        await message.answer("Использование:\n/price BTC")
        return

    try:
        resolved = await resolver.resolve(command[1], interval="1h")
        data = await market.provider.get_ticker(resolved.base)
    except Exception as exc:
        await message.answer(f"❌ Инструмент OKX не найден или временно недоступен.\n\n{exc}")
        return

    change = data["change_percent_24h"]
    change_icon = "🟢" if change >= 0 else "🔴"
    text = (
        f"📈 <b>{data['instrument_id']}</b>\n"
        f"Exchange: OKX Futures\n\n"
        f"💲 Цена: <b>{_fmt(data['last'])}</b>\n"
        f"{change_icon} 24ч: <b>{change:+.2f}%</b>\n\n"
        f"⬆ High: {_fmt(data['high_24h'])}\n"
        f"⬇ Low: {_fmt(data['low_24h'])}\n"
        f"📦 Volume: {_fmt(data['volume_contracts_24h'])} contracts"
    )
    await message.answer(text, parse_mode="HTML")
