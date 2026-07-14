from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def analysis_actions_keyboard(symbol: str, timeframe: str = "1h") -> InlineKeyboardMarkup:
    symbol = symbol.upper().strip()
    kb = InlineKeyboardBuilder()
    kb.button(text="🧠 Explain Pro", callback_data=f"explain:{symbol}:{timeframe}")
    kb.button(text="🚫 Why NOT?", callback_data=f"whynot:{symbol}:{timeframe}")
    kb.button(text="📐 Technical", callback_data=f"technical:{symbol}:{timeframe}")
    kb.button(text="🧭 Scenarios", callback_data=f"scenarios:{symbol}:{timeframe}")
    kb.button(text="📚 History", callback_data=f"history:{symbol}:{timeframe}")
    kb.button(text="🧩 Similar", callback_data=f"similar:{symbol}:{timeframe}")
    kb.button(text="⭐ Watch", callback_data=f"watch:{symbol}:{timeframe}")
    kb.button(text="🔄 Refresh", callback_data=f"refresh:{symbol}:{timeframe}")
    kb.adjust(2, 2, 2, 2)
    return kb.as_markup()
