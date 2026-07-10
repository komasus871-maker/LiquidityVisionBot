import html
import logging
from aiogram import Bot

from utils.price import fmt_price


class Notifier:
    def __init__(self, bot: Bot | None = None):
        self.bot = bot

    async def lifecycle(self, signal: dict, event: str, price: float, extra: str = "") -> None:
        if not self.bot:
            return
        chat_id = signal.get("notification_chat_id") or signal.get("owner_telegram_id")
        if not chat_id:
            return
        icons = {
            "TRIGGERED": "🔔", "ACTIVE": "🟢", "TP1": "🎯", "TP2": "🎯", "TP3": "🏆",
            "STOP": "🛑", "INVALIDATED": "⚠️", "EXPIRED": "⌛",
        }
        titles = {
            "TRIGGERED": "Цена вошла в preferred entry zone",
            "ACTIVE": "Сетап активирован",
            "TP1": "TP1 достигнут",
            "TP2": "TP2 достигнут",
            "TP3": "TP3 достигнут — сделка закрыта",
            "STOP": "Stop Loss достигнут",
            "INVALIDATED": "Сетап инвалидирован",
            "EXPIRED": "Сетап истёк",
        }
        text = f"""
{icons.get(event, '🔔')} <b>{html.escape(signal['symbol'])} {html.escape(signal['side'])}</b>

<b>{titles.get(event, event)}</b>
Signal ID: <code>{signal['id']}</code>
Price: <code>{fmt_price(price)}</code>
Status: <b>{event}</b>
"""
        if extra:
            text += f"\n{html.escape(extra)}\n"
        try:
            await self.bot.send_message(chat_id, text, parse_mode="HTML")
        except Exception:
            logging.exception("Failed to send lifecycle notification for signal %s", signal.get("id"))
