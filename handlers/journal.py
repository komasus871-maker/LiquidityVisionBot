from aiogram import Router, F
from aiogram.types import Message

from database.signal_history import SignalHistory
from utils.price import fmt_price

router = Router()
history = SignalHistory()


def _status_icon(status: str) -> str:
    return {
        "WATCHING": "👀", "TRIGGERED": "🔔", "ACTIVE": "⚡", "TP1": "🎯", "TP2": "🎯",
        "TP3": "🏆", "STOP": "🛑", "INVALIDATED": "⚠️", "EXPIRED": "⌛",
    }.get(status, "•")


@router.message(F.text == "📒 Journal")
async def journal_handler(message: Message):
    user_id = message.from_user.id
    stats = history.get_stats(user_id)
    recent = history.get_recent(user_id, limit=8)

    recent_text = []
    for item in recent:
        recent_text.append(
            f"{_status_icon(item['status'])} <b>#{item['id']} {item['symbol']} {item['side']}</b> — {item['status']}\n"
            f"   Entry {fmt_price(item['entry'])} | RR 1:{item['rr']:.2f} | MFE {float(item.get('max_profit_pct') or 0):.2f}%"
        )

    timeline = "\n\n".join(recent_text) if recent_text else "Пока нет сохранённых сетапов. Анализ с подходящим статусом появится здесь автоматически."

    await message.answer(f"""
📒 <b>Trade Journal PRO</b>

👀 Watching: {stats.get('watching_count') or 0}
🔔 Triggered: {stats.get('triggered_count') or 0}
⚡ Active: {stats.get('active_count') or 0}
✅ Closed: {stats.get('closed_count') or 0}
📚 Total tracked: {stats.get('total') or 0}

🏆 Win rate by TP1: {stats.get('win_rate') or 0}%
🎯 TP1 rate: {stats.get('tp1_rate') or 0}%
🎯 TP2 rate: {stats.get('tp2_rate') or 0}%
🎯 TP3 rate: {stats.get('tp3_rate') or 0}%
🛑 Stops: {stats.get('stop_hits') or 0}
⚠️ Invalidated: {stats.get('invalidated_count') or 0}
⌛ Expired: {stats.get('expired_count') or 0}

📈 Average MFE: {round(stats.get('avg_mfe') or 0, 2)}%
📉 Average MAE: {round(stats.get('avg_mae') or 0, 2)}%

━━━━━━━━━━━━━━━━━━

🕘 <b>Recent lifecycle</b>

{timeline}
""", parse_mode="HTML")
