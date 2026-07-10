from aiogram import Router, F
from aiogram.types import Message
from services.scanner import Scanner

router = Router(); scanner = Scanner()


@router.message(F.text == "📈 Market")
async def market_handler(message: Message):
    wait = await message.answer("📈 Формирую обзор рынка...")
    data = await scanner.market_overview()
    leaders = data["results"][:5]
    lines = "\n".join(
        f"• {x['symbol']}: {x['market_bias']} | Dir {x['confidence']} | Ready {x['readiness']} | {x['direction']}"
        for x in leaders
    ) or "• Нет данных"
    interpretation = (
        "Структура и breadth растут быстрее готовности входов: рынок восстанавливается, но chasing опасен."
        if "Recovery" in data['regime'] else
        "Большинство активов подтверждают направление и имеют приемлемую готовность исполнения."
        if "Expansion" in data['regime'] else
        "Рынок ротационный: предпочтительны избирательные сделки и подтверждение по каждой монете."
    )
    await wait.edit_text(f"""
📈 <b>Market Intelligence</b>

Market Regime: {data['regime']}
Bullish breadth: {data['breadth']}%
LONG / SHORT: {data['long_count']} / {data['short_count']}
READY setups: {data['ready_count']}
Average direction score: {data['avg_score']}/100
Average execution readiness: {data['avg_readiness']}/100

🧠 <b>Interpretation</b>
{interpretation}

🏅 <b>Market Leaders</b>
{lines}
""", parse_mode="HTML")
