from aiogram import Router, F
from aiogram.types import Message
from services.scanner import Scanner

router = Router()
scanner = Scanner()


@router.message(F.text == "📈 Market")
async def market_handler(message: Message):
    wait = await message.answer("📈 Формирую обзор рынка...")
    data = await scanner.market_overview()
    leaders = data["results"][:5]
    lines = "\n".join(
        f"• {x['symbol']}: {x['market_bias']} | {x['confidence']}/100 | {x['direction']}"
        for x in leaders
    ) or "• Нет данных"
    await wait.edit_text(
        f"""
📈 <b>Market Overview</b>

Режим рынка: {data['regime']}
Bullish breadth: {data['breadth']}%
LONG scenarios: {data['long_count']}
SHORT scenarios: {data['short_count']}
READY setups: {data['ready_count']}
Average setup score: {data['avg_score']}/100

🏅 <b>Market Leaders</b>
{lines}
""",
        parse_mode="HTML",
    )
