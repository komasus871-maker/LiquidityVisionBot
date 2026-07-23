from __future__ import annotations

import os
from decimal import Decimal
from html import escape

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from services.exchanges.base import ExchangeError
from services.exchanges.models import ExchangeName, ExchangeStatus
from services.exchanges.registry import build_exchange_registry

router = Router()
registry = build_exchange_registry()

_STATUS_LABELS = {
    ExchangeStatus.CONNECTED: "🟢 CONNECTED",
    ExchangeStatus.PUBLIC_ONLY: "🟡 PUBLIC ONLY",
    ExchangeStatus.NOT_CONFIGURED: "⚪ NOT CONFIGURED",
    ExchangeStatus.GEO_BLOCKED: "🔴 GEO BLOCKED",
    ExchangeStatus.AUTH_FAILED: "🟠 AUTH FAILED",
    ExchangeStatus.UNAVAILABLE: "🔴 UNAVAILABLE",
}


def _money(value: Decimal) -> str:
    return f"{value:,.4f}".rstrip("0").rstrip(".")


def _default_exchange() -> ExchangeName:
    raw = os.getenv("EXCHANGE_DEFAULT", "okx").strip().lower()
    try:
        candidate = ExchangeName(raw)
    except ValueError:
        candidate = ExchangeName.OKX
    return candidate if candidate in registry.available() else registry.available()[0]


def _parse_exchange(parts: list[str]) -> tuple[ExchangeName, list[str]]:
    if parts:
        try:
            name = ExchangeName(parts[0].lower())
        except ValueError:
            pass
        else:
            if name in registry.available():
                return name, parts[1:]
    return _default_exchange(), parts


async def _adapter_call(exchange: ExchangeName, operation: str, *args):
    adapter = registry.create(exchange)
    try:
        return await getattr(adapter, operation)(*args)
    finally:
        await adapter.close()


@router.message(Command("exchanges"))
async def exchanges_status(message: Message) -> None:
    lines = [
        "🔌 <b>Exchange Foundation</b>", "",
        "v9.8.5 adds a read-only BingX USDT-M adapter with reachability diagnostics and resilient public transport.",
        "No adapter can place, modify, or cancel orders.", "",
    ]
    for exchange in registry.available():
        health = await _adapter_call(exchange, "health")
        environment = "TESTNET" if health.testnet else "PRODUCTION"
        latency = f" · {health.latency_ms:.0f} ms" if health.latency_ms is not None else ""
        default = " · DEFAULT" if exchange is _default_exchange() else ""
        lines.append(f"• <b>{exchange.value.upper()}</b> — {_STATUS_LABELS[health.status]} · {environment}{latency}{default}")
        if health.endpoint:
            lines.append(f"  Endpoint: <code>{escape(health.endpoint)}</code>")
        if health.error and health.error != "credentials_not_configured":
            lines.append(f"  <code>{escape(health.error[:180])}</code>")
    lines.extend([
        "", "<b>Commands</b>",
        "<code>/exchange_balance [okx|bingx|bybit|binance]</code>",
        "<code>/exchange_positions [okx|bingx|bybit|binance]</code>",
        "<code>/exchange_orders [okx|bingx|bybit|binance] [SYMBOL]</code>",
        "<code>/exchange_symbol [okx|bingx|bybit|binance] BTCUSDT</code>", "",
        "🔒 API secrets stay in environment variables and are never stored in the database.",
    ])
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("exchange_balance"))
async def exchange_balance(message: Message) -> None:
    exchange, _ = _parse_exchange((message.text or "").split()[1:])
    try:
        balances = await _adapter_call(exchange, "balances")
    except ExchangeError as exc:
        await message.answer(f"⚠️ <b>{exchange.value.title()} balance unavailable</b>\n<code>{escape(str(exc))}</code>", parse_mode="HTML")
        return
    rows = [f"• <b>{escape(i.asset)}</b> · wallet {_money(i.wallet_balance)} · available {_money(i.available_balance)}" for i in balances]
    await message.answer(f"💰 <b>{exchange.value.title()} balances</b>\n\n" + ("\n".join(rows) if rows else "No non-zero balances."), parse_mode="HTML")


@router.message(Command("exchange_positions"))
async def exchange_positions(message: Message) -> None:
    exchange, _ = _parse_exchange((message.text or "").split()[1:])
    try:
        positions = await _adapter_call(exchange, "positions")
    except ExchangeError as exc:
        await message.answer(f"⚠️ <b>{exchange.value.title()} positions unavailable</b>\n<code>{escape(str(exc))}</code>", parse_mode="HTML")
        return
    rows = [f"• <b>{escape(i.symbol)} {i.side}</b> · qty {_money(i.quantity)} · entry {_money(i.entry_price)} · PnL {_money(i.unrealized_pnl)} · {i.leverage}x" for i in positions]
    await message.answer(f"📌 <b>{exchange.value.title()} positions</b>\n\n" + ("\n".join(rows) if rows else "No open positions."), parse_mode="HTML")


@router.message(Command("exchange_orders"))
async def exchange_orders(message: Message) -> None:
    exchange, args = _parse_exchange((message.text or "").split()[1:])
    symbol = args[0].upper() if args else None
    try:
        orders = await _adapter_call(exchange, "open_orders", symbol)
    except ExchangeError as exc:
        await message.answer(f"⚠️ <b>{exchange.value.title()} orders unavailable</b>\n<code>{escape(str(exc))}</code>", parse_mode="HTML")
        return
    rows = [f"• <b>{escape(i.symbol)} {i.side}</b> · {escape(i.order_type)} · qty {_money(i.quantity)} · filled {_money(i.executed_quantity)}" for i in orders]
    await message.answer(f"📋 <b>{exchange.value.title()} open orders</b>\n\n" + ("\n".join(rows) if rows else "No open orders."), parse_mode="HTML")


@router.message(Command("exchange_symbol"))
async def exchange_symbol(message: Message) -> None:
    exchange, args = _parse_exchange((message.text or "").split()[1:])
    if not args:
        await message.answer("Usage: <code>/exchange_symbol [okx|bingx|bybit|binance] BTCUSDT</code>\nOKX accepts <code>BTC-USDT-SWAP</code>; BingX normalizes to <code>BTC-USDT</code>.", parse_mode="HTML")
        return
    symbol = args[0].upper()
    try:
        rules = await _adapter_call(exchange, "symbol_rules", symbol)
    except ExchangeError as exc:
        await message.answer(f"⚠️ <b>{exchange.value.title()} symbol rules unavailable</b>\n<code>{escape(str(exc))}</code>", parse_mode="HTML")
        return
    minimum = _money(rules.min_notional) if rules.min_notional is not None else "not published"
    await message.answer(
        f"⚙️ <b>{exchange.value.title()} · {escape(rules.symbol)} execution rules</b>\n\n"
        f"Status: <b>{escape(rules.status)}</b>\nPair: <b>{escape(rules.base_asset)}/{escape(rules.quote_asset)}</b>\n"
        f"Price tick: <code>{_money(rules.price_tick)}</code>\nQuantity step: <code>{_money(rules.quantity_step)}</code>\n"
        f"Minimum quantity: <code>{_money(rules.min_quantity)}</code>\nMinimum notional: <code>{minimum}</code>", parse_mode="HTML")
