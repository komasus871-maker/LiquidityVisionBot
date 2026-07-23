from __future__ import annotations

from decimal import Decimal
from html import escape

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from services.exchanges.base import ExchangeError
from services.exchanges.models import ExchangeName
from services.exchanges.registry import build_exchange_registry

router = Router()
registry = build_exchange_registry()


def _money(value: Decimal) -> str:
    return f"{value:,.4f}".rstrip("0").rstrip(".")


async def _adapter_call(exchange: ExchangeName, operation: str, *args):
    adapter = registry.create(exchange)
    try:
        method = getattr(adapter, operation)
        return await method(*args)
    finally:
        await adapter.close()


@router.message(Command("exchanges"))
async def exchanges_status(message: Message) -> None:
    lines = [
        "🔌 <b>Exchange Foundation</b>",
        "",
        "v9.8.1 introduces a read-only exchange contract. No adapter can place, modify, or cancel orders.",
        "",
    ]
    for exchange in registry.available():
        health = await _adapter_call(exchange, "health")
        environment = "TESTNET" if health.testnet else "PRODUCTION"
        if health.reachable and health.authenticated:
            state = "🟢 CONNECTED"
        elif health.reachable:
            state = "🟡 PUBLIC ONLY"
        else:
            state = "🔴 UNREACHABLE"
        latency = f" · {health.latency_ms:.0f} ms" if health.latency_ms is not None else ""
        lines.append(f"• <b>{exchange.value.upper()}</b> — {state} · {environment}{latency}")
        if health.error and health.error != "credentials_not_configured":
            lines.append(f"  <code>{escape(health.error[:180])}</code>")
    lines.extend(
        [
            "",
            "<b>Commands</b>",
            "<code>/exchange_balance</code> — non-zero futures balances",
            "<code>/exchange_positions</code> — open futures positions",
            "<code>/exchange_orders [SYMBOL]</code> — open futures orders",
            "<code>/exchange_symbol BTCUSDT</code> — tick, step and minimum rules",
            "",
            "🔒 API secrets are read only from environment variables and are never stored in the database.",
        ]
    )
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("exchange_balance"))
async def exchange_balance(message: Message) -> None:
    try:
        balances = await _adapter_call(ExchangeName.BINANCE, "balances")
    except ExchangeError as exc:
        await message.answer(f"⚠️ <b>Binance balance unavailable</b>\n<code>{escape(str(exc))}</code>", parse_mode="HTML")
        return
    rows = [
        f"• <b>{escape(item.asset)}</b> · wallet {_money(item.wallet_balance)} · available {_money(item.available_balance)}"
        for item in balances
    ]
    await message.answer("💰 <b>Binance USD-M balances</b>\n\n" + ("\n".join(rows) if rows else "No non-zero balances."), parse_mode="HTML")


@router.message(Command("exchange_positions"))
async def exchange_positions(message: Message) -> None:
    try:
        positions = await _adapter_call(ExchangeName.BINANCE, "positions")
    except ExchangeError as exc:
        await message.answer(f"⚠️ <b>Binance positions unavailable</b>\n<code>{escape(str(exc))}</code>", parse_mode="HTML")
        return
    rows = [
        f"• <b>{escape(item.symbol)} {item.side}</b> · qty {_money(item.quantity)} · "
        f"entry {_money(item.entry_price)} · PnL {_money(item.unrealized_pnl)} · {item.leverage}x"
        for item in positions
    ]
    await message.answer("📌 <b>Binance USD-M positions</b>\n\n" + ("\n".join(rows) if rows else "No open positions."), parse_mode="HTML")


@router.message(Command("exchange_orders"))
async def exchange_orders(message: Message) -> None:
    parts = (message.text or "").split(maxsplit=1)
    symbol = parts[1].strip().upper() if len(parts) > 1 else None
    try:
        orders = await _adapter_call(ExchangeName.BINANCE, "open_orders", symbol)
    except ExchangeError as exc:
        await message.answer(f"⚠️ <b>Binance orders unavailable</b>\n<code>{escape(str(exc))}</code>", parse_mode="HTML")
        return
    rows = [
        f"• <b>{escape(item.symbol)} {item.side}</b> · {escape(item.order_type)} · "
        f"qty {_money(item.quantity)} · filled {_money(item.executed_quantity)}"
        for item in orders
    ]
    await message.answer("📋 <b>Binance USD-M open orders</b>\n\n" + ("\n".join(rows) if rows else "No open orders."), parse_mode="HTML")


@router.message(Command("exchange_symbol"))
async def exchange_symbol(message: Message) -> None:
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer("Usage: <code>/exchange_symbol BTCUSDT</code>", parse_mode="HTML")
        return
    symbol = parts[1].strip().upper()
    try:
        rules = await _adapter_call(ExchangeName.BINANCE, "symbol_rules", symbol)
    except ExchangeError as exc:
        await message.answer(f"⚠️ <b>Symbol rules unavailable</b>\n<code>{escape(str(exc))}</code>", parse_mode="HTML")
        return
    minimum = _money(rules.min_notional) if rules.min_notional is not None else "not published"
    await message.answer(
        f"⚙️ <b>{escape(rules.symbol)} execution rules</b>\n\n"
        f"Status: <b>{escape(rules.status)}</b>\n"
        f"Pair: <b>{escape(rules.base_asset)}/{escape(rules.quote_asset)}</b>\n"
        f"Price tick: <code>{_money(rules.price_tick)}</code>\n"
        f"Quantity step: <code>{_money(rules.quantity_step)}</code>\n"
        f"Minimum quantity: <code>{_money(rules.min_quantity)}</code>\n"
        f"Minimum notional: <code>{minimum}</code>",
        parse_mode="HTML",
    )
