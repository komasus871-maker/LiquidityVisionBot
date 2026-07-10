from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message


from database.database import connect
router = Router()


@router.message(Command("profile"))
async def profile(message: Message):

    conn = connect()

    cursor = conn.cursor()

    cursor.execute("""
        SELECT
        premium,
        created_at
        FROM users
        WHERE telegram_id=?
    """, (message.from_user.id,))

    user = cursor.fetchone()

    conn.close()

    if user is None:

        await message.answer(
            "Профиль не найден."
        )

        return

    premium = "✅ Да" if user[0] else "❌ Нет"

    text = f"""
👤 Профиль

ID:
<code>{message.from_user.id}</code>

Username:
@{message.from_user.username}

Premium:
{premium}

Дата регистрации:

{user[1]}
"""

    await message.answer(text)
