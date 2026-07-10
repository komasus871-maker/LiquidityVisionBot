from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from services.news import NewsEngine

router = Router()
engine = NewsEngine()


def _age(minutes: int | None) -> str:
    if minutes is None:
        return "time unknown"
    if minutes < 60:
        return f"{minutes}m ago"
    if minutes < 1440:
        return f"{minutes // 60}h ago"
    return f"{minutes // 1440}d ago"


def _render(items: list[dict], title: str = "News Intelligence") -> str:
    if not items:
        return "📰 <b>News Intelligence</b>\n\nНе удалось получить свежие новости. Источники временно недоступны."
    blocks: list[str] = []
    for item in items:
        coins = ", ".join(item.get("coins") or []) or "Market-wide"
        blocks.append(
            f"{item['impact']} · {item['sentiment']} · <b>{item['confidence']}%</b>\n"
            f"<a href=\"{escape(item['url'], quote=True)}\"><b>{escape(item['title'])}</b></a>\n"
            f"{escape(item['source'])} · {_age(item.get('age_minutes'))}\n"
            f"Affected: <b>{escape(coins)}</b>"
        )
    return f"📰 <b>{title}</b>\n\n" + "\n\n━━━━━━━━━━━━━━━━━━\n\n".join(blocks)


async def send_news(message: Message) -> None:
    wait = await message.answer("📰 Собираю и классифицирую новости...")
    items = await engine.latest(limit=10)
    await wait.edit_text(_render(items), parse_mode="HTML", disable_web_page_preview=True)


@router.message(Command("news"))
async def news_command(message: Message):
    await send_news(message)


@router.message(F.text == "📰 News")
async def news_handler(message: Message):
    await send_news(message)
