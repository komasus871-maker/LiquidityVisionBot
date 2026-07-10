from aiogram.utils.keyboard import InlineKeyboardBuilder


def analysis_actions(symbol: str, signal_id: int | None = None):
    kb = InlineKeyboardBuilder()
    kb.button(text="🧠 Explain Pro", callback_data=f"explain_{symbol}_{signal_id or 0}")
    kb.button(text="🔄 Refresh", callback_data=f"analyze_{symbol}")
    kb.adjust(2)
    return kb.as_markup()
