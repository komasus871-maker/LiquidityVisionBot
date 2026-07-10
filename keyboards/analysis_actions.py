from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def analysis_actions_keyboard(symbol: str) -> InlineKeyboardMarkup:
    symbol = symbol.upper().strip()
    kb = InlineKeyboardBuilder()
    kb.button(text="🧠 Explain Pro", callback_data=f"explain_{symbol}")
    kb.button(text="🔄 Refresh", callback_data=f"refresh_{symbol}")
    kb.adjust(2)
    return kb.as_markup()
