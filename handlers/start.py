from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from database.database import add_user
from keyboards.main_menu import main_keyboard

router = Router()


@router.message(Command("start"))
async def start(message: Message):
    add_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    first_name = message.from_user.first_name or "trader"
    text = (
        "<b>Liquidity Vision Intelligence</b>\n\n"
        f"Welcome, <b>{first_name}</b>. This bot turns market structure, liquidity, momentum, "
        "derivatives, and historical evidence into bounded decision support.\n\n"
        "Start with:\n"
        "• <code>/analyze BTC 1h</code> — a market analysis\n"
        "• <code>/scanner</code> — ranked opportunities\n"
        "• <code>/watchlist add BTC SOL</code> — personalized tracking\n"
        "• <code>/help</code> — the complete categorized catalog\n\n"
        "Copy execution is PAPER by default: simulated orders, risk controls, and lifecycle accounting. "
        "Free is useful core intelligence; Pro adds depth and personalization; Elite adds advanced research.\n\n"
        "This is evidence and decision support—not a prediction, trading authority, or promise of profit."
    )
    await message.answer(text, reply_markup=main_keyboard())
