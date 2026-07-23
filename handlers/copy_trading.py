from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from services.copy_trading import CopyTradingService

router = Router()
service = CopyTradingService()


def _status_text(profile: dict, stats: dict) -> str:
    enabled = "🟢 ENABLED" if profile.get("enabled") else "🔴 DISABLED"
    return f"""🤖 <b>Liquidity Vision Copy Execution</b>

Status: {enabled}
Mode: 🧪 <b>PAPER</b>
Paper balance: <b>${float(profile['paper_balance']):,.2f}</b>
Risk per trade: <b>{float(profile['risk_pct']):.2f}%</b>
Max positions: <b>{int(profile['max_positions'])}</b>
Max portfolio heat: <b>{float(profile['max_heat_r']):.2f}R</b>
Daily loss limit: <b>{float(profile['daily_loss_pct']):.2f}%</b>
Max slippage: <b>{float(profile['max_slippage_pct']):.2f}%</b>
Min confidence: <b>{float(profile.get('min_confidence') or 55):.0f}%</b>
Max notional: <b>{float(profile.get('max_notional_pct') or 35):.0f}% of equity</b>
Symbol cooldown: <b>{int(profile.get('symbol_cooldown_min') or 30)} min</b>

📊 <b>Paper execution</b>
Open: {int(stats.get('open_count') or 0)}
Closed: {int(stats.get('closed_count') or 0)}
Rejected: {int(stats.get('rejected_count') or 0)}
Equity: <b>${float(stats.get('equity') or profile['paper_balance']):,.2f}</b>
Today: <b>${float(stats.get('daily_pnl') or 0):+,.2f}</b>
Total PnL: <b>${float(stats.get('realized_pnl') or 0):+,.2f}</b>
Total realized: {float(stats.get('realized_r') or 0):+.2f}R
Average: {float(stats.get('avg_r') or 0):+.2f}R
Win rate: {float(stats.get('win_rate') or 0):.1f}%

<b>Commands</b>
<code>/copy_enable</code> — start paper copying
<code>/copy_disable</code> — pause new entries
<code>/copy_risk 0.5</code> — risk per trade
<code>/copy_balance 10000</code> — paper balance
<code>/copy_limits 3 2.5 2</code> — positions, heat R, daily loss %
<code>/copy_guard 55 35 30 0.25</code> — confidence, notional %, cooldown min, slippage %
<code>/copy_stats</code> — execution statistics
<code>/panic</code> — close paper positions and disable execution

🔒 LIVE execution remains fail-closed. v9.2.0 uses a full paper execution ledger."""


@router.message(Command("copy"))
async def copy_status(message: Message):
    profile = service.ensure_profile(message.from_user.id)
    await message.answer(_status_text(profile, service.profile_stats(message.from_user.id)), parse_mode="HTML")


@router.message(Command("copy_enable"))
async def copy_enable(message: Message):
    profile = service.update_profile(message.from_user.id, enabled=1)
    await message.answer("🟢 <b>Paper copy execution enabled.</b> New ACTIVE signals will be validated and copied with your risk profile.", parse_mode="HTML")


@router.message(Command("copy_disable"))
async def copy_disable(message: Message):
    service.update_profile(message.from_user.id, enabled=0)
    await message.answer("⏸ <b>Copy execution paused.</b> Existing paper positions remain tracked.", parse_mode="HTML")


@router.message(Command("copy_risk"))
async def copy_risk(message: Message):
    try:
        value = float((message.text or "").split(maxsplit=1)[1].replace(",", "."))
        if not 0.05 <= value <= 5:
            raise ValueError
    except (IndexError, ValueError):
        await message.answer("Usage: <code>/copy_risk 0.5</code>\nAllowed: 0.05–5%", parse_mode="HTML")
        return
    service.update_profile(message.from_user.id, risk_pct=value)
    await message.answer(f"✅ Risk per trade set to <b>{value:.2f}%</b>.", parse_mode="HTML")


@router.message(Command("copy_balance"))
async def copy_balance(message: Message):
    try:
        value = float((message.text or "").split(maxsplit=1)[1].replace(",", "."))
        if not 100 <= value <= 100_000_000:
            raise ValueError
    except (IndexError, ValueError):
        await message.answer("Usage: <code>/copy_balance 10000</code>", parse_mode="HTML")
        return
    service.update_profile(message.from_user.id, paper_balance=value)
    await message.answer(f"✅ Paper balance set to <b>${value:,.2f}</b>.", parse_mode="HTML")


@router.message(Command("copy_limits"))
async def copy_limits(message: Message):
    try:
        parts = (message.text or "").split()
        positions, heat, daily = int(parts[1]), float(parts[2]), float(parts[3])
        if not (1 <= positions <= 20 and 1 <= heat <= 20 and 0.5 <= daily <= 25):
            raise ValueError
    except (IndexError, ValueError):
        await message.answer("Usage: <code>/copy_limits 3 2.5 2</code>\n(max positions, max heat R, daily loss %)", parse_mode="HTML")
        return
    service.update_profile(message.from_user.id, max_positions=positions, max_heat_r=heat, daily_loss_pct=daily)
    await message.answer("✅ Copy risk limits updated.", parse_mode="HTML")


@router.message(Command("copy_stats"))
async def copy_stats(message: Message):
    profile = service.ensure_profile(message.from_user.id)
    await message.answer(_status_text(profile, service.profile_stats(message.from_user.id)), parse_mode="HTML")


@router.message(Command("panic"))
async def panic(message: Message):
    count = service.panic(message.from_user.id)
    await message.answer(f"🛑 <b>PANIC completed.</b>\nCopy execution disabled. Paper positions closed: <b>{count}</b>.", parse_mode="HTML")


@router.message(Command("copy_guard"))
async def copy_guard(message: Message):
    try:
        parts = (message.text or "").split()
        confidence, notional, cooldown, slippage = float(parts[1]), float(parts[2]), int(parts[3]), float(parts[4])
        if not (0 <= confidence <= 100 and 1 <= notional <= 100 and 0 <= cooldown <= 1440 and 0 <= slippage <= 5):
            raise ValueError
    except (IndexError, ValueError):
        await message.answer(
            "Usage: <code>/copy_guard 55 35 30 0.25</code>\n"
            "(minimum confidence %, max notional %, symbol cooldown minutes, max slippage %)",
            parse_mode="HTML",
        )
        return
    service.update_profile(
        message.from_user.id, min_confidence=confidence, max_notional_pct=notional,
        symbol_cooldown_min=cooldown, max_slippage_pct=slippage,
    )
    await message.answer("✅ Execution guardrails updated.", parse_mode="HTML")
