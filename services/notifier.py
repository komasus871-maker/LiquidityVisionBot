from __future__ import annotations

import html
import logging
from datetime import datetime, timezone

from aiogram import Bot

from services.probability_engine import ProbabilityEngine
from utils.price import fmt_price


class Notifier:
    def __init__(self, bot: Bot | None = None):
        self.bot = bot
        self.probability = ProbabilityEngine()

    @staticmethod
    def _duration(started_at: str | None) -> str | None:
        if not started_at:
            return None
        try:
            start = datetime.fromisoformat(started_at)
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            seconds = max(0, int((datetime.now(timezone.utc) - start).total_seconds()))
        except (TypeError, ValueError):
            return None
        hours, remainder = divmod(seconds, 3600)
        minutes = remainder // 60
        if hours:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"

    @staticmethod
    def _progress(signal: dict, price: float) -> float:
        entry = float(signal.get("entry") or 0)
        if not entry:
            return 0.0
        move = (price - entry) / entry * 100
        return round(move if signal.get("side") == "LONG" else -move, 2)

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
            "INVALIDATED": "Сетап инвалидирован до активации",
            "EXPIRED": "Сетап истёк без активации",
        }

        progress = self._progress(signal, price)
        duration = self._duration(signal.get("activated_at") or signal.get("created_at"))
        stats = self.probability.exact_stats(
            str(signal.get("setup_key") or ""),
            str(signal.get("timeframe") or "1h"),
            str(signal.get("side") or "LONG"),
        )

        lines = [
            f"{icons.get(event, '🔔')} <b>{html.escape(str(signal['symbol']))} {html.escape(str(signal['side']))}</b>",
            "",
            f"<b>{titles.get(event, event)}</b>",
            f"Signal ID: <code>{signal['id']}</code>",
            f"Price: <code>{fmt_price(price)}</code>",
            f"Status: <b>{event}</b>",
        ]
        if event in {"ACTIVE", "TP1", "TP2", "TP3", "STOP"}:
            lines.append(f"Current move: <b>{progress:+.2f}%</b>")
        if duration:
            lines.append(f"Duration: <b>{duration}</b>")
        if event == "TRIGGERED":
            lines.append("Next: waiting for a directional reaction candle.")
        elif event == "ACTIVE":
            lines.append(f"Targets: {fmt_price(float(signal['tp1']))} / {fmt_price(float(signal['tp2']))} / {fmt_price(float(signal['tp3']))}")
        elif event == "TP1":
            lines.append(f"Next target: <b>{fmt_price(float(signal['tp2']))}</b>")
        elif event == "TP2":
            lines.append(f"Final target: <b>{fmt_price(float(signal['tp3']))}</b>")
        elif event in {"TP3", "STOP"}:
            lines.append(f"MFE: <b>{float(signal.get('max_profit_pct') or 0):.2f}%</b>")
            lines.append(f"MAE: <b>{float(signal.get('max_drawdown_pct') or 0):.2f}%</b>")

        if int(stats.get("samples") or 0) >= 5:
            lines.extend([
                "",
                "📚 <b>Historical exact-setup context</b>",
                f"Samples: {stats['samples']} · TP1 {stats['tp1_rate']}% · TP2 {stats['tp2_rate']}% · Stop {stats['stop_rate']}%",
                f"Reliability: {html.escape(str(stats['reliability']))}",
            ])
        if extra:
            lines.extend(["", html.escape(extra)])

        try:
            await self.bot.send_message(chat_id, "\n".join(lines), parse_mode="HTML")
        except Exception:
            logging.exception("Failed to send lifecycle notification for signal %s", signal.get("id"))
