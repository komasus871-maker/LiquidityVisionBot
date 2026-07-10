from aiogram import Router, F
from aiogram.types import Message
from services.news import NewsEngine

router = Router()
engine = NewsEngine()


@router.message(F.text == "📰 News")
async def news_handler(message: Message):
    items = await engine.latest()
    if not items or items[0].get("title") == "Coming Soon":
        await message.answer(
            "📰 <b>News Intelligence</b>\n\nМодуль источников пока не подключён. Архитектура готова: далее добавим RSS/API, оценку bullish/bearish impact, важность и привязку к монетам.",
            parse_mode="HTML",
        )
        return
    text = "📰 <b>Latest Market News</b>\n\n" + "\n\n".join(f"• {x['title']}" for x in items[:10])
    await message.answer(text, parse_mode="HTML")
