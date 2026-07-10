from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message

from database.signal_history import SignalHistory
from services.premium import PremiumService

router = Router()
history = SignalHistory()
premium = PremiumService()


def _bar(value: float, width: int = 10) -> str:
    value = max(0.0, min(100.0, float(value)))
    filled = round(value / 100 * width)
    return "█" * filled + "░" * (width - filled)


async def send_dashboard(message: Message):
    stats = history.get_stats(message.from_user.id)
    recent = history.get_recent(message.from_user.id, limit=5)
    p = premium.status(message.from_user.id)
    open_total = (stats.get("watching_count") or 0) + (stats.get("triggered_count") or 0) + (stats.get("active_count") or 0)
    recent_lines = []
    for item in recent:
        recent_lines.append(f"• #{item['id']} {item['symbol']} {item['side']} — {item['status']}")
    recent_text = "\n".join(recent_lines) if recent_lines else "• Пока нет сохранённых сетапов"

    win = float(stats.get("win_rate") or 0)
    tp1 = float(stats.get("tp1_rate") or 0)
    tp2 = float(stats.get("tp2_rate") or 0)
    tp3 = float(stats.get("tp3_rate") or 0)

    await message.answer(f"""
📊 <b>Liquidity Vision Dashboard</b>

👑 Plan: <b>{p['tier'] if p['active'] else 'FREE'}</b>
📚 Tracked: <b>{stats.get('total') or 0}</b>
👀 Open lifecycle: <b>{open_total}</b>
✅ Closed: <b>{stats.get('closed_count') or 0}</b>

🏆 <b>Performance</b>
Win rate  {_bar(win)} {win:.1f}%
TP1       {_bar(tp1)} {tp1:.1f}%
TP2       {_bar(tp2)} {tp2:.1f}%
TP3       {_bar(tp3)} {tp3:.1f}%

📈 Average MFE: <b>{float(stats.get('avg_mfe') or 0):.2f}%</b>
📉 Average MAE: <b>{float(stats.get('avg_mae') or 0):.2f}%</b>

🕘 <b>Recent activity</b>
{recent_text}

🧠 Historical probabilities become reliable after at least 30 completed similar setups.
""", parse_mode="HTML")


@router.message(Command("dashboard"))
async def dashboard_command(message: Message):
    await send_dashboard(message)


@router.message(F.text == "📊 Dashboard")
async def dashboard_button(message: Message):
    await send_dashboard(message)
