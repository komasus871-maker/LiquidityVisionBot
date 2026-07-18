from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message

from database.database import connect, add_user
from database.signal_history import SignalHistory
from services.premium import PremiumService

router = Router()
history = SignalHistory()
premium_service = PremiumService()


async def send_profile(message: Message):
    add_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    with connect() as conn:
        user = conn.execute("SELECT created_at FROM users WHERE telegram_id=?", (message.from_user.id,)).fetchone()
    stats = history.get_stats(message.from_user.id)
    premium = premium_service.status(message.from_user.id)
    username = f"@{message.from_user.username}" if message.from_user.username else "не указан"
    premium_text = f"✅ {premium['tier']} до {premium['until']}" if premium["active"] else "❌ FREE"

    await message.answer(f"""
👤 <b>Trader Profile</b>

Name: {message.from_user.first_name}
Username: {username}
ID: <code>{message.from_user.id}</code>
Premium: {premium_text}
Registered: {user[0] if user else 'today'}

📊 <b>Personal Statistics</b>
👀 Watching: {stats.get('watching_count') or 0}
🔔 Triggered: {stats.get('triggered_count') or 0}
⚡ Active: {stats.get('active_count') or 0}
✅ Closed: {stats.get('closed_count') or 0}
📚 Total tracked: {stats.get('total') or 0}

🏆 Resolved Win Rate: {stats.get('win_rate') or 0}%
✅ Wins / Losses: {stats.get('wins') or 0} / {stats.get('losses') or 0}
🛡 Break Even: {stats.get('breakeven_count') or 0}
❓ Unclassified closed: {stats.get('unclassified_count') or 0}
🛑 Manual closes: {stats.get('manual_close_count') or 0}
🎯 TP1 / TP2 / TP3 progression: {stats.get('tp1_rate') or 0}% / {stats.get('tp2_rate') or 0}% / {stats.get('tp3_rate') or 0}%
⚖️ Average realized: {round(stats.get('avg_realized_r') or 0, 2)}R
📈 Average MFE: {round(stats.get('avg_mfe') or 0, 2)}%
📉 Average MAE: {round(stats.get('avg_mae') or 0, 2)}%

🔔 Lifecycle notifications: {'✅' if premium['notifications'] else '❌'}
""", parse_mode="HTML")


@router.message(Command("profile"))
async def profile_command(message: Message):
    await send_profile(message)


@router.message(F.text == "👤 Profile")
async def profile_button(message: Message):
    await send_profile(message)
