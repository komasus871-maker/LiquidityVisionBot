from aiogram import Router, F
from aiogram.types import Message
from database.signal_history import SignalHistory

router = Router(); history = SignalHistory()


@router.message(F.text == "📒 Journal")
async def journal_handler(message: Message):
    stats = history.get_stats()
    total = stats.get('total') or 0
    tp2_rate = round((stats.get('tp2_hits') or 0) / total * 100, 2) if total else 0
    tp3_rate = round((stats.get('tp3_hits') or 0) / total * 100, 2) if total else 0
    await message.answer(f"""
📒 <b>Trade Journal PRO</b>

👀 Watching: {stats.get('watching_count') or 0}
⚡ Active: {stats.get('active_count') or 0}
✅ Closed: {stats.get('closed_count') or 0}
📚 Total tracked: {total}

🎯 TP1 rate: {stats.get('tp1_rate') or 0}%
🎯 TP2 rate: {tp2_rate}%
🎯 TP3 rate: {tp3_rate}%
🛑 Stops: {stats.get('stop_hits') or 0}

📈 Average MFE: {round(stats.get('avg_mfe') or 0, 2)}%
📉 Average MAE: {round(stats.get('avg_mae') or 0, 2)}%

Статистика разделяет наблюдаемые сетапы и реально активированные сделки.
""", parse_mode="HTML")
