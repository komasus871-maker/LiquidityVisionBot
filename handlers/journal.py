from aiogram import Router, F
from aiogram.types import Message
from database.signal_history import SignalHistory

router = Router()
history = SignalHistory()


@router.message(F.text == "📒 Journal")
async def journal_handler(message: Message):
    stats = history.get_stats()
    await message.answer(
        f"""
📒 <b>Signal Journal</b>

All signals: {stats.get('total') or 0}
Open: {stats.get('open_count') or 0}
TP1 hits: {stats.get('tp1_hits') or 0}
TP2 hits: {stats.get('tp2_hits') or 0}
TP3 hits: {stats.get('tp3_hits') or 0}
Stops: {stats.get('stop_hits') or 0}
TP1 hit rate: {stats.get('tp1_rate') or 0}%
Average MFE: {round(stats.get('avg_mfe') or 0, 2)}%
Average MAE: {round(stats.get('avg_mae') or 0, 2)}%

Статистика будет становиться точнее по мере накопления завершённых сигналов.
""",
        parse_mode="HTML",
    )
