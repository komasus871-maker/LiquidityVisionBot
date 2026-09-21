from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

from handlers.copy_trading import copy_status, execution_positions
from handlers.intelligence_hub import portfolio_handler
from handlers.preferences import settings
from handlers.research import signal_rankings
from services.forward_runtime_state import ForwardRuntimeStateRepository
from services.market_terminal import (
    render_market_overview, render_order_flow, render_shadow_status, render_system_status,
)
from services.webhook_server import resolve_public_base_url


router = Router()
state = ForwardRuntimeStateRepository()


def _symbol(message: Message) -> str:
    parts = (message.text or "").split(maxsplit=1)
    value = parts[1] if len(parts) == 2 else "BTCUSDT"
    normalized = value.upper().replace("/", "").replace("-", "")
    return normalized if normalized in {"BTCUSDT", "ETHUSDT", "SOLUSDT"} else "BTCUSDT"


@router.message(Command("market_now"))
@router.message(F.text == "🧠 Market Intelligence")
@router.message(F.text == "📊 Markets")
async def market_terminal(message: Message) -> None:
    await message.answer(
        render_market_overview(state.latest_states(("BTCUSDT", "ETHUSDT", "SOLUSDT"))),
        parse_mode="HTML",
    )


@router.message(Command("orderflow"))
@router.message(F.text == "🌊 Order Flow")
async def order_flow(message: Message) -> None:
    symbol = _symbol(message)
    await message.answer(
        render_order_flow(state.latest_states((symbol,)), symbol), parse_mode="HTML"
    )


@router.message(Command("shadow_status"))
@router.message(F.text == "🧪 Shadow")
@router.message(F.text == "🧪 Shadow Lab")
async def shadow_status(message: Message) -> None:
    await message.answer(render_shadow_status(state.health()), parse_mode="HTML")


@router.message(Command("terminal_health"))
@router.message(F.text == "🩺 System")
async def terminal_health(message: Message) -> None:
    from services.operational_runtime import OperationalHealthRepository
    await message.answer(
        render_system_status(state.health(), OperationalHealthRepository().health()),
        parse_mode="HTML",
    )


@router.message(F.text == "📝 Paper")
@router.message(F.text == "📈 Paper")
async def paper_terminal(message: Message) -> None:
    await copy_status(message)


@router.message(F.text == "📚 Positions")
async def positions_terminal(message: Message) -> None:
    await execution_positions(message)


@router.message(F.text == "🎯 Signals")
async def signals_terminal(message: Message) -> None:
    await signal_rankings(message)


@router.message(F.text == "⚙️ Settings")
@router.message(F.text == "⚙ Settings")
async def settings_terminal(message: Message) -> None:
    await settings(message)


@router.message(F.text == "🛡 Risk")
async def risk_terminal(message: Message) -> None:
    await portfolio_handler(message)


@router.message(Command("terminal"))
@router.message(F.text == "🚀 Open Terminal")
async def open_terminal(message: Message) -> None:
    url = f"{resolve_public_base_url()}/terminal"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🚀 Open Liquidity Vision Terminal", web_app=WebAppInfo(url=url))
    ]])
    await message.answer(
        "🚀 <b>LIQUIDITY VISION TERMINAL</b>\n\n"
        "Read-only market intelligence, scanner episodes, order flow, derivatives, PAPER state, "
        "Shadow health, and system status. No order controls are exposed.",
        parse_mode="HTML", reply_markup=keyboard,
    )
