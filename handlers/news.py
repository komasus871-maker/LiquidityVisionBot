from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.types import Message

from services.news import NewsEngine

router = Router()
engine = NewsEngine()


def _format_news(items: list[dict]) -> str:
    if not items:
        return (
            "📰 <b>News Intelligence</b>\n\n"
            "Не удалось получить новости прямо сейчас. Источники могут временно быть недоступны. "
            "Попробуй ещё раз через несколько минут."
        )

    high = sum(1 for x in items if x.get("impact") == "🔴 HIGH")
    impact_weights = {"🔴 HIGH": 3, "🟡 MEDIUM": 2, "⚪ LOW": 1}
    sentiment_score = 0
    directional_weight = 0
    for item in items:
        weight = impact_weights.get(item.get("impact", "⚪ LOW"), 1)
        sentiment = item.get("sentiment", "")
        if "Bullish" in sentiment:
            sentiment_score += weight
            directional_weight += weight
        elif "Bearish" in sentiment:
            sentiment_score -= weight
            directional_weight += weight
    ratio = (sentiment_score / directional_weight) if directional_weight else 0
    if ratio >= 0.45:
        overall = "🟢 Bullish tilt"
    elif ratio <= -0.45:
        overall = "🔴 Bearish tilt"
    elif ratio > 0.12:
        overall = "🟡 Slight bullish / mostly neutral"
    elif ratio < -0.12:
        overall = "🟠 Slight bearish / mostly neutral"
    else:
        overall = "⚪ Mixed / Neutral"

    chunks = [
        "📰 <b>News Intelligence</b>",
        "",
        f"High impact: <b>{high}</b>",
        f"Headline sentiment: <b>{overall}</b>",
        "",
        "━━━━━━━━━━━━━━━━━━",
    ]

    for index, item in enumerate(items[:8], start=1):
        coins = ", ".join(item.get("coins") or ["General market"])
        title = escape(item.get("title", "Untitled"))
        source = escape(item.get("source", "Unknown"))
        url = item.get("url", "")
        linked_title = f'<a href="{escape(url, quote=True)}">{title}</a>' if url else title
        chunks.extend([
            "",
            f"{index}. {item.get('impact', '⚪ LOW')} · {item.get('sentiment', '⚪ Neutral')}",
            f"<b>{linked_title}</b>",
            f"Coins: {escape(coins)}",
            f"Impact confidence: {int(item.get('confidence', 50))}%",
            f"Source: {source}",
        ])

    chunks.extend([
        "",
        "━━━━━━━━━━━━━━━━━━",
        "<i>News classification is heuristic and is not financial advice.</i>",
    ])
    return "\n".join(chunks)


@router.message(F.text == "📰 News")
async def news_handler(message: Message):
    waiting = await message.answer("📰 Загружаю и оцениваю последние новости…")
    try:
        items = await engine.latest(limit=10)
        await waiting.edit_text(_format_news(items), parse_mode="HTML", disable_web_page_preview=True)
    except Exception as exc:
        await waiting.edit_text(
            "📰 <b>News Intelligence</b>\n\n"
            "Источники сейчас недоступны или вернули некорректный ответ. "
            f"Ошибка: <code>{escape(type(exc).__name__)}</code>",
            parse_mode="HTML",
        )
