from aiogram.utils.keyboard import ReplyKeyboardBuilder


def main_keyboard():

    kb = ReplyKeyboardBuilder()

    buttons = [
        "📊 Markets",
        "🔍 Analyze",
        "⚡ Scanner",
        "🎯 Signals",
        "🌊 Order Flow",
        "📈 Paper",
        "🧪 Shadow Lab",
        "💼 Portfolio",
        "🛡 Risk",
        "🔔 Alerts",
        "⚙ Settings",
        "🩺 System",
        "🚀 Open Terminal",

    ]

    for button in buttons:

        kb.button(text=button)

    kb.adjust(2)

    return kb.as_markup(

        resize_keyboard=True

    )
