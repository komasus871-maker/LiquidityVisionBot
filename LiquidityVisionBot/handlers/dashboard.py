from __future__ import annotations

import asyncio
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from database.signal_history import SignalHistory
from services.news import NewsEngine
from services.premium import PremiumService
from services.scanner import Scanner
from utils.fear_greed import FearGreed

router = Router()
history = SignalHistory()
premium = PremiumService()
scanner = Scanner()
news = NewsEngine()
fear = FearGreed()


def _bar(value: float, width: int = 10) -> str:
    value = max(0.0, min(100.0, float(value)))
    filled = round(value / 100 * width)
    return "█" * filled + "░" * (width - filled)


async def _safe_market() -> dict:
    try:
        return await asyncio.wait_for(scanner.market_overview(), timeout=25)
    except Exception:
        return {}


async def _safe_fear() -> tuple[int | None, str]:
    try:
        return await asyncio.wait_for(fear.get(), timeout=8)
    except Exception:
        return None, "Unavailable"


async def _safe_news() -> list[dict]:
    try:
        return await asyncio.wait_for(news.high_impact(limit=3), timeout=15)
    except Exception:
        return []


async def send_dashboard(message: Message):
    loading = await message.answer("📊 Собираю Intelligence Dashboard...")
    stats = history.get_stats(message.from_user.id)
    recent = history.get_recent(message.from_user.id, limit=5)
    plan = premium.status(message.from_user.id)
    market, fear_data, high_news = await asyncio.gather(_safe_market(), _safe_fear(), _safe_news())

    open_total = sum(int(stats.get(key) or 0) for key in ("watching_count", "triggered_count", "active_count"))
    recent_text = "\n".join(f"• #{item['id']} {item['symbol']} {item['side']} — {item['status']}" for item in recent) or "• Пока нет сохранённых сетапов"
    win = float(stats.get("win_rate") or 0)
    tp1 = float(stats.get("tp1_rate") or 0)
    tp2 = float(stats.get("tp2_rate") or 0)
    tp3 = float(stats.get("tp3_rate") or 0)

    if market:
        ready = [x for x in market.get("results", []) if x.get("category") == "READY_NOW"][:3]
        ready_text = "\n".join(f"• {x['symbol']} {x['direction']} — Dir {x['confidence']:.1f} / Ready {x['readiness']:.1f}" for x in ready) or "• Готовых входов сейчас нет"
        market_text = (
            f"Regime: <b>{market.get('regime')}</b>\n"
            f"Breadth: <b>{market.get('breadth')}%</b> · LONG/SHORT: <b>{market.get('long_count')}/{market.get('short_count')}</b>\n"
            f"Ready setups: <b>{market.get('ready_count')}</b> · Avg readiness: <b>{market.get('avg_readiness')}</b>"
        )
    else:
        ready_text = "• Scanner data unavailable"
        market_text = "Market data temporarily unavailable"

    fear_value, fear_label = fear_data
    fear_text = f"<b>{fear_value}</b> — {escape(fear_label)}" if fear_value is not None else "Unavailable"
    news_text = "\n".join(f"• {item['sentiment']} {escape(item['title'][:85])}" for item in high_news) or "• No high-impact headlines loaded"

    await loading.edit_text(f"""
📊 <b>Liquidity Vision Intelligence Dashboard</b>

🌍 <b>Market</b>
{market_text}

😨 <b>Sentiment</b>
Fear & Greed: {fear_text}

🚀 <b>Ready Now</b>
{ready_text}

📰 <b>High-Impact News</b>
{news_text}

📒 <b>Your Lifecycle</b>
Plan: <b>{plan['tier'] if plan['active'] else 'FREE'}</b>
Tracked: <b>{stats.get('total') or 0}</b> · Open: <b>{open_total}</b> · Closed: <b>{stats.get('closed_count') or 0}</b>

🏆 <b>Performance</b>
Win  {_bar(win)} {win:.1f}%
TP1  {_bar(tp1)} {tp1:.1f}%
TP2  {_bar(tp2)} {tp2:.1f}%
TP3  {_bar(tp3)} {tp3:.1f}%
MFE: <b>{float(stats.get('avg_mfe') or 0):.2f}%</b> · MAE: <b>{float(stats.get('avg_mae') or 0):.2f}%</b>

🕘 <b>Recent Activity</b>
{recent_text}
""", parse_mode="HTML", disable_web_page_preview=True)


@router.message(Command("dashboard"))
async def dashboard_command(message: Message):
    await send_dashboard(message)


@router.message(F.text == "📊 Dashboard")
async def dashboard_button(message: Message):
    await send_dashboard(message)
