from aiogram.utils.keyboard import InlineKeyboardBuilder


def analyze_keyboard():

    kb = InlineKeyboardBuilder()

    coins = [
        "BTC",
        "ETH",
        "SOL",
        "BNB",
        "XRP",
        "ADA",
        "DOGE",
        "LINK",
        "AVAX",
        "SUI",
        "ONDO",
        "ARB",
    ]

    for coin in coins:
        kb.button(
            text=f"🪙 {coin}",
            callback_data=f"analyze_{coin}"
        )

    kb.adjust(3)

    return kb.as_markup()