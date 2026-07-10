from aiogram.utils.keyboard import ReplyKeyboardBuilder


def main_keyboard():

    kb = ReplyKeyboardBuilder()

    buttons = [

        "📊 Analyze",

        "🔥 Scanner",

        "📈 Market",

        "😨 Fear",

        "📰 News",

        "📒 Journal",

        "👤 Profile"

    ]

    for button in buttons:

        kb.button(text=button)

    kb.adjust(2)

    return kb.as_markup(

        resize_keyboard=True

    )