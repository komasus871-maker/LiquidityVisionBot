from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message
from database.database import connect, add_user
from database.signal_history import SignalHistory

router = Router()
history = SignalHistory()


async def send_profile(message: Message):
    add_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    conn = connect(); cursor = conn.cursor()
    cursor.execute("SELECT premium, created_at FROM users WHERE telegram_id=?", (message.from_user.id,))
    user = cursor.fetchone(); conn.close()
    stats = history.get_stats()
    premium = "✅ Да" if user and user[0] else "❌ Нет"
    username = f"@{message.from_user.username}" if message.from_user.username else "не указан"
    await message.answer(
        f"""
👤 <b>Trader Profile</b>

Name: {message.from_user.first_name}
Username: {username}
ID: <code>{message.from_user.id}</code>
Premium: {premium}
Registered: {user[1] if user else 'today'}

📊 <b>System Statistics</b>
Tracked signals: {stats.get('total') or 0}
Open signals: {stats.get('open_count') or 0}
TP1 rate: {stats.get('tp1_rate') or 0}%
""",
        parse_mode="HTML",
    )


@router.message(Command("profile"))
async def profile_command(message: Message):
    await send_profile(message)


@router.message(F.text == "👤 Profile")
async def profile_button(message: Message):
    await send_profile(message)
