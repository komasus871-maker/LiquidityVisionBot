from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

router = Router()

@router.message(Command("help"))
async def help_command(message: Message):
    await message.answer("""📚 <b>LIQUIDITY VISION · COMMAND CENTER</b>

<b>🔎 Market analysis</b>
/analyze BTC — полный анализ монеты
/price BTC — текущая цена
/scanner — поиск сильных сетапов
/market — обзор рынка
/news — новости
/fear — Fear & Greed / настроение

<b>🧠 Intelligence</b>
/performance — expectancy, PF, серии и результаты
/portfolio — позиции, effective risk и концентрация
/dna — сильные и слабые торговые когорты
/insights — единый краткий intelligence brief

<b>📒 Journal & lifecycle</b>
/journal — Trade Journal PRO
/trade 105 — подробности и replay сделки
/trade 105 close — закрыть активную сделку
/trade all close или /closeall — закрыть все активные
/trade stats audit — аудит статистики и дублей

<b>⭐ Personal</b>
/watchlist — личный watchlist
/profile — профиль и статистика
/premium — статус и возможности Premium
/start — главное меню
/help — этот список

<b>🛠 Admin</b>
/admin_status — база, workers и integrity
/workers — подробная диагностика workers

Примеры: <code>/analyze BTC</code> · <code>/trade 105</code> · <code>/portfolio</code>""", parse_mode="HTML")
