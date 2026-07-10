from aiogram.utils.keyboard import InlineKeyboardBuilder


def timeframe_keyboard(symbol: str):
    kb = InlineKeyboardBuilder()
    for tf in ("15m", "1h", "4h", "1d"):
        kb.button(text=tf.upper(), callback_data=f"ua:{symbol}:{tf}")
    kb.adjust(4)
    return kb.as_markup()
