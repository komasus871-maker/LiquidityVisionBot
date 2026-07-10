from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message
from database.database import connect, add_user
from database.signal_history import SignalHistory

router = Router(); history = SignalHistory()


async def send_profile(message: Message):
    add_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    conn = connect(); cursor = conn.cursor()
    cursor.execute("SELECT premium, created_at FROM users WHERE telegram_id=?", (message.from_user.id,))
    user = cursor.fetchone(); conn.close(); stats = history.get_stats()
    premium = "✅ Да" if user and user[0] else "❌ Нет"
    username = f"@{message.from_user.username}" if message.from_user.username else "не указан"
    await message.answer(f"""
👤 <b>Trader Profile</b>

Name: {message.from_user.first_name}
Username: {username}
ID: <code>{message.from_user.id}</code>
Premium: {premium}
Registered: {user[1] if user else 'today'}

📊 <b>System Statistics</b>
Watching setups: {stats.get('watching_count') or 0}
Active trades: {stats.get('active_count') or 0}
Closed trades: {stats.get('closed_count') or 0}
Total tracked: {stats.get('total') or 0}
TP1 rate: {stats.get('tp1_rate') or 0}%
Average MFE: {round(stats.get('avg_mfe') or 0, 2)}%
Average MAE: {round(stats.get('avg_mae') or 0, 2)}%
""", parse_mode="HTML")


@router.message(Command("profile"))
async def profile_command(message: Message): await send_profile(message)

@router.message(F.text == "👤 Profile")
async def profile_button(message: Message): await send_profile(message)
