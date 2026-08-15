from __future__ import annotations

import os
import json
import logging
from decimal import Decimal
from datetime import datetime, timezone
from html import escape

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from database.database import connect

from services.exchanges.base import ExchangeConfigurationError, ExchangeError
from services.exchanges.manager import ExchangeManager
from services.exchanges.execution import DemoExecutionManager, DemoOrderRequest
from services.exchanges.models import ExchangeCredentials, ExchangeName, ExchangeStatus
from services.exchanges.safety import (
    ExecutionSafetyPolicy,
    ExecutionSafetyValidator,
    OrderIntent,
    OrderSide,
)
from services.exchanges.registry import build_exchange_registry
from services.exchanges.credentials_store import CredentialCipher, UserExchangeCredentialStore
from services.live_accounts import LiveAccountRepository
from services.execution_models import ExecutionMode
from services.live_readiness import ReadinessContext, audit_readiness
from services.bingx_certification import BingXCertificationService, live_certification_valid
from services.execution_portfolio import ExecutionPortfolioEngine
from services.bingx_sync import BingXAccountSyncService, BingXSyncReport
from services.public_errors import public_error_message

router = Router()
logger = logging.getLogger(__name__)
_EMPTY_CREDENTIALS = {
    exchange: ExchangeCredentials("", "", testnet=True)
    for exchange in (ExchangeName.BINANCE, ExchangeName.BYBIT, ExchangeName.BINGX, ExchangeName.OKX)
}
registry = build_exchange_registry(credentials_override=_EMPTY_CREDENTIALS, okx_passphrase="")
manager = ExchangeManager(
    registry,
    operation_timeout_seconds=float(os.getenv("EXCHANGE_OPERATION_TIMEOUT", "25")),
)
execution_manager = DemoExecutionManager(
    registry, timeout_seconds=float(os.getenv("EXCHANGE_OPERATION_TIMEOUT", "25"))
)
live_accounts = LiveAccountRepository()


def format_bingx_certification(report) -> str:
    blockers = ", ".join(report.readiness_blockers) or "none"
    return (
        f"🧪 <b>BingX {escape(report.certification_type)} certification</b>\n\n"
        f"Status: <b>{escape(report.status)}</b>\nEnvironment: <code>{escape(report.environment)}</code>\n"
        f"Adapter: <code>{escape(report.adapter_version)}</code>\nCredentials: <b>{escape(report.credential_status)}</b>\n"
        f"Account mode: <code>{escape(report.account_mode)}</code> · Margin: <code>{escape(report.margin_mode)}</code>\n"
        f"Available funds: <code>{escape(report.available_funds)}</code>\n"
        f"Positions / orders: <code>{report.open_positions} / {report.open_orders}</code>\n"
        f"Server drift: <code>{report.server_time_drift_ms} ms</code>\n"
        f"Symbol: <code>{escape(report.symbol)}</code> · qty <code>{escape(str(report.normalized_quantity))}</code> "
        f"@ <code>{escape(str(report.normalized_price))}</code>\n"
        f"Economic order calls: <b>{report.order_submission_calls}</b>\n"
        f"Blockers: <code>{escape(blockers)}</code>"
    )


async def _run_bingx_dry_certification(message: Message, *, symbol: str, quantity: Decimal,
                                      price: Decimal):
    account = live_accounts.ensure(message.from_user.id, ExchangeName.BINGX.value)
    user_registry = _user_registry(message.from_user.id, ExchangeName.BINGX)
    adapter = user_registry.create(ExchangeName.BINGX)
    try:
        return await BingXCertificationService(adapter).dry_run(
            telegram_id=message.from_user.id, account_id=account.id, symbol=symbol,
            sample_quantity=quantity, sample_price=price,
            expected_environment="prod-vst" if adapter.credentials.testnet else "prod-live",
        )
    finally:
        await adapter.close()


@router.message(Command("live_sync"))
async def live_sync(message: Message) -> None:
    exchange, args = _parse_exchange((message.text or "").split()[1:])
    if exchange is not ExchangeName.BINGX:
        await message.answer("LIVE account synchronization is currently certified only for BingX.")
        return
    symbol = args[0].upper() if args else os.getenv("BINGX_CERTIFICATION_SYMBOL", "BTCUSDT")
    account = live_accounts.ensure(message.from_user.id, exchange.value)
    adapter = None
    try:
        user_registry = _user_registry(message.from_user.id, exchange)
        adapter = user_registry.create(exchange)
        report = await BingXAccountSyncService(adapter).synchronize(
            telegram_id=message.from_user.id, account_id=account.id, symbol=symbol)
    except ExchangeError as exc:
        await message.answer(
            f"⚠️ <b>BingX synchronization failed before adapter sync</b>\n"
            f"{public_error_message(exc, context='EXCHANGE')}", parse_mode="HTML")
        return
    finally:
        if adapter is not None:
            try:
                await adapter.close()
            except Exception as exc:
                logger.warning("bingx_sync adapter_cleanup_failed type=%s", type(exc).__name__)
    if not report.success:
        await message.answer(
            f"⚠️ <b>BingX synchronization failed</b>\n\n"
            f"Stage: <code>{escape(report.stage)}</code>\n"
            f"Reason: <code>{escape(report.error_code or 'UNAVAILABLE')}</code>\n"
            f"Environment: <code>{escape(report.environment)}</code>\n"
            f"Adapter: <code>{escape(report.adapter_version)}</code>", parse_mode="HTML")
        return
    await message.answer(
        f"✅ <b>BingX synchronization complete</b>\n\n"
        f"Adapter: <code>{escape(report.adapter_version)}</code>\n"
        f"Environment: <code>{escape(report.environment)}</code>\n"
        f"Last successful sync: <code>{escape(report.synchronized_at or 'unknown')}</code>\n"
        f"Server drift: <code>{report.server_time_drift_ms} ms</code>\n"
        f"Account / margin mode: <code>{escape(report.account_mode or 'unknown')} / "
        f"{escape(report.margin_mode or 'unknown')}</code>\n"
        f"Available funds: <code>{escape(report.available_funds)}</code>\n"
        f"Positions / orders: <code>{report.open_positions} / {report.open_orders}</code>\n"
        f"Capabilities: <code>{report.capability_count}</code>\n"
        f"Symbol: <code>{escape(report.symbol or symbol)}</code>", parse_mode="HTML")


@router.message(Command("live_certify"))
async def live_certify(message: Message) -> None:
    if getattr(message.chat, "type", "private") != "private":
        await message.answer("⛔ BingX certification is available only in a private chat.")
        return
    exchange, args = _parse_exchange((message.text or "").split()[1:])
    if exchange is not ExchangeName.BINGX:
        await message.answer("Certification is currently implemented only for BingX.")
        return
    if args and args[0].lower() == "execute":
        if len(args) != 2 or args[1] != "CERTIFY_VST":
            await message.answer(
                "⚠️ VST economic certification requires exactly "
                "<code>/live_certify bingx execute CERTIFY_VST</code>.", parse_mode="HTML")
            return
        account = live_accounts.ensure(message.from_user.id, exchange.value)
        adapter = None
        try:
            user_registry = _user_registry(message.from_user.id, exchange)
            adapter = user_registry.create(exchange)
            report = await BingXCertificationService(adapter).certify_vst_economic(
                telegram_id=message.from_user.id, account_id=account.id,
                symbol=os.getenv("BINGX_CERTIFICATION_SYMBOL", "BTCUSDT"),
                quantity=Decimal(os.getenv("BINGX_CERTIFICATION_QUANTITY", "0.001")),
                reference_price=Decimal(os.getenv("BINGX_CERTIFICATION_REFERENCE_PRICE", "60000")),
                confirmation=args[1],
            )
        except (ExchangeError, ValueError, PermissionError) as exc:
            await message.answer(f"⚠️ {public_error_message(exc, context='EXCHANGE')}", parse_mode="HTML")
            return
        finally:
            if adapter is not None:
                await adapter.close()
        await message.answer(format_bingx_certification(report), parse_mode="HTML")
        return
    symbol = args[0].upper() if args else os.getenv("BINGX_CERTIFICATION_SYMBOL", "BTCUSDT")
    try:
        quantity = Decimal(args[1]) if len(args) > 1 else Decimal(os.getenv("BINGX_CERTIFICATION_QUANTITY", "0.001"))
        price = Decimal(args[2]) if len(args) > 2 else Decimal(os.getenv("BINGX_CERTIFICATION_REFERENCE_PRICE", "60000"))
        report = await _run_bingx_dry_certification(message, symbol=symbol, quantity=quantity, price=price)
    except (ExchangeError, ValueError) as exc:
        await message.answer(f"⚠️ {public_error_message(exc, context='EXCHANGE')}", parse_mode="HTML")
        return
    await message.answer(format_bingx_certification(report), parse_mode="HTML")


@router.message(Command("live_account"))
async def live_account(message: Message) -> None:
    exchange, _ = _parse_exchange((message.text or "").split()[1:])
    account = live_accounts.ensure(message.from_user.id, exchange.value)
    unresolved = live_accounts.unresolved(message.from_user.id, exchange.value)
    await message.answer(
        f"🔐 <b>{escape(exchange.value.title())} live account</b>\n\n"
        f"Environment: <code>{escape(account.adapter_environment or 'not synchronized')}</code>\n"
        f"Adapter: <code>{escape(account.adapter_version or 'unknown')}</code>\n"
        f"Mode: <code>{account.execution_mode.value}</code>\n"
        f"Account / margin mode: <code>{escape(account.account_mode or 'unknown')} / {escape(account.margin_mode or 'unknown')}</code>\n"
        f"Last sync: <code>{escape(account.last_sync_at or 'never')}</code>\n"
        f"Sync status: <code>{escape(account.sync_status or 'never')} · {escape(account.sync_stage or 'none')}</code>\n"
        f"Sync error: <code>{escape(account.sync_error_code or 'none')}</code>\n"
        f"Time drift: <code>{account.server_time_drift_ms if account.server_time_drift_ms is not None else 'unknown'} ms</code>\n"
        f"Certification: <code>{escape(account.certification_status or 'none')}</code>\n"
        f"Unresolved executions: <b>{len(unresolved)}</b>\n"
        f"Kill switch: <b>{'ACTIVE' if account.kill_switch else 'RELEASED'}</b>", parse_mode="HTML")


@router.message(Command("live_dry_run"))
async def live_dry_run(message: Message) -> None:
    exchange, args = _parse_exchange((message.text or "").split()[1:])
    enabled = bool(args and args[0].lower() in {"on", "enable", "enabled"})
    account = live_accounts.set_dry_run(message.from_user.id, exchange.value, enabled)
    await message.answer(
        f"🧪 <b>LIVE_DRY_RUN {'enabled' if enabled else 'disabled'}</b> for {escape(exchange.value)}.\n"
        f"Mode: <code>{account.execution_mode.value}</code>\nNo economic orders can be submitted in this mode.",
        parse_mode="HTML",
    )


@router.message(Command("live_confirm"))
async def live_confirm(message: Message) -> None:
    if getattr(message.chat, "type", "private") != "private":
        await message.answer("⛔ Live confirmation is available only in a private chat.")
        return
    exchange, args = _parse_exchange((message.text or "").split()[1:])
    if args:
        confirmed = live_accounts.confirm(message.from_user.id, exchange.value, args[0])
        await message.answer(
            "✅ Confirmation recorded. LIVE remains disabled and the kill switch remains active."
            if confirmed else "⚠️ Confirmation code is invalid or expired."
        )
        return
    token = live_accounts.begin_confirmation(message.from_user.id, exchange.value)
    await message.answer(
        f"⚠️ <b>Step 1 of 2</b> for {escape(exchange.value)}\n"
        f"Confirm within 10 minutes with <code>/live_confirm {escape(exchange.value)} {token}</code>.\n"
        "This records intent only; it does not enable LIVE or release the kill switch.", parse_mode="HTML")


@router.message(Command("live_disable"))
async def live_disable(message: Message) -> None:
    exchange, _ = _parse_exchange((message.text or "").split()[1:])
    live_accounts.emergency_disable(message.from_user.id, exchange.value)
    await message.answer(f"🛑 {escape(exchange.value)} execution disabled. Kill switch is active.")


@router.message(Command("recovery"))
async def recovery_status(message: Message) -> None:
    exchange, _ = _parse_exchange((message.text or "").split()[1:])
    rows = live_accounts.unresolved(message.from_user.id, exchange.value)
    if not rows:
        await message.answer(f"✅ No unresolved {escape(exchange.value)} live executions.")
        return
    lines = [f"⚠️ <b>{len(rows)} unresolved execution(s)</b>"]
    lines.extend(f"<code>#{row['id']} {escape(row['symbol'])} {row['state']} {escape(row['client_order_id'])}</code>" for row in rows[:10])
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("live_readiness"))
async def live_readiness(message: Message) -> None:
    exchange, _ = _parse_exchange((message.text or "").split()[1:])
    account = live_accounts.ensure(message.from_user.id, exchange.value)
    metadata = live_accounts.readiness_metadata(account.id)
    credentials = live_accounts.credentials_present(message.from_user.id, exchange.value)
    unresolved = live_accounts.unresolved(message.from_user.id, exchange.value)
    adapter_capabilities = registry.create(exchange).capabilities()
    try:
        permissions = json.loads(metadata.get("permission_snapshot_json") or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        permissions = {}
    synced_at = None
    if account.last_sync_at:
        try:
            synced_at = datetime.fromisoformat(account.last_sync_at.replace("Z", "+00:00"))
            if synced_at.tzinfo is None:
                synced_at = synced_at.replace(tzinfo=timezone.utc)
        except ValueError:
            synced_at = None
    sync_fresh = bool(synced_at and (datetime.now(timezone.utc) - synced_at).total_seconds()
                      <= int(os.getenv("BINGX_SYNC_MAX_AGE_SECONDS", "900")))
    portfolio = ExecutionPortfolioEngine().snapshot(message.from_user.id)
    with connect() as conn:
        profile = conn.execute("SELECT daily_loss_pct FROM copy_profiles WHERE telegram_id=?",
                               (message.from_user.id,)).fetchone()
    daily_loss_guard = bool(profile and float(profile["daily_loss_pct"] or 0) > 0)
    readiness = audit_readiness(
        telegram_id=message.from_user.id, account_id=account.id, exchange=exchange.value,
        mode=ExecutionMode.LIVE, context=ReadinessContext(
        environment=os.getenv("ENVIRONMENT", "local"),
        feature_flag=os.getenv("LIVE_EXECUTION_ENABLED", "false").lower() in {"1", "true", "yes", "on"},
        account_enabled=account.live_enabled, confirmed=bool(account.confirmed_at),
        credentials_present=credentials, trading_permission=bool(permissions.get("trading")),
        withdrawal_enabled=permissions.get("withdrawal"), account_synced=sync_fresh,
        server_time_synced=(account.server_time_drift_ms is not None and
                            abs(account.server_time_drift_ms) <= int(os.getenv("BINGX_MAX_SERVER_DRIFT_MS", "1500"))),
        symbol_rules_valid=bool(metadata.get("valid_symbol_rules")),
        portfolio_resolved=portfolio.resolved, recovery_required=len(unresolved),
        reconciliation_safe=portfolio.resolved and not unresolved,
        daily_loss_protection=daily_loss_guard,
        max_order_notional=account.max_order_notional,
        max_account_exposure=account.max_account_exposure, max_leverage=account.max_leverage,
        kill_switch_available=True, kill_switch_active=account.kill_switch,
        recent_certification=live_certification_valid(
            account.id, environment=os.getenv("BINGX_LIVE_CERTIFICATION_ENVIRONMENT", "prod-vst")
        ) if exchange is ExchangeName.BINGX else False,
        production_adapter_allowed=(exchange is ExchangeName.BINGX and
                                    os.getenv("BINGX_PRODUCTION_ADAPTER_ALLOWED", "false").lower() in {"1", "true", "yes", "on"}),
        account_mode_known=account.account_mode in {"HEDGE", "ONE_WAY"},
        capabilities=adapter_capabilities,
    ))
    reasons = ", ".join(readiness.reason_codes[:8]) or "READY"
    await message.answer(
        f"🛡 <b>Live readiness · {escape(exchange.value)}</b>\n\n"
        f"Mode: <code>{account.execution_mode.value}</code>\n"
        f"Credentials present: <b>{'YES' if credentials else 'NO'}</b>\n"
        f"Two-step confirmed: <b>{'YES' if account.confirmed_at else 'NO'}</b>\n"
        f"Account enabled: <b>{'YES' if account.live_enabled else 'NO'}</b>\n"
        f"Kill switch: <b>{'ACTIVE' if account.kill_switch else 'RELEASED'}</b>\n"
        f"Unresolved/retry executions: <b>{len(unresolved)}</b>\n"
        f"Max order / exposure / leverage: <code>{account.max_order_notional or 'unset'} / "
        f"{account.max_account_exposure or 'unset'} / {account.max_leverage or 'unset'}</code>\n\n"
        f"Readiness: <b>{'READY' if readiness.ready else 'BLOCKED'}</b>\n"
        f"Reasons: <code>{escape(reasons)}</code>\n\n"
        "LIVE is fail-closed until every server-side readiness gate passes.", parse_mode="HTML")


def _credential_store() -> UserExchangeCredentialStore:
    return UserExchangeCredentialStore(CredentialCipher())


def _user_registry(telegram_id: int, exchange: ExchangeName):
    connection = _credential_store().get(telegram_id, exchange)
    if connection is None:
        raise ExchangeConfigurationError(
            f"{exchange.value} is not connected for your Telegram account; use /connect_exchange"
        )
    return build_exchange_registry(
        credentials_override={exchange: connection.credentials},
        okx_passphrase=connection.passphrase if exchange is ExchangeName.OKX else None,
    )


async def _user_adapter_call(telegram_id: int, exchange: ExchangeName, operation: str, *args):
    user_registry = _user_registry(telegram_id, exchange)
    adapter = user_registry.create(exchange)
    try:
        return await getattr(adapter, operation)(*args)
    finally:
        await adapter.close()


def _user_execution_manager(telegram_id: int, exchange: ExchangeName) -> DemoExecutionManager:
    manager = DemoExecutionManager(
        _user_registry(telegram_id, exchange),
        timeout_seconds=float(os.getenv("EXCHANGE_OPERATION_TIMEOUT", "25")),
    )
    if os.getenv(f"USER_EXECUTION_KILLED_{telegram_id}", "").lower() == "true":
        manager.kill()
    return manager

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


@router.message(Command("connect_exchange"))
async def connect_exchange(message: Message) -> None:
    parts = (message.text or "").split()
    if getattr(message.chat, "type", "private") != "private":
        await message.answer("⛔ Connect exchange accounts only in a private chat with the bot.")
        return
    try:
        await message.delete()
    except Exception:
        pass
    if len(parts) < 5:
        await message.answer(
            "🔐 <b>Connect your own exchange account</b>\n\n"
            "BingX: <code>/connect_exchange bingx demo API_KEY API_SECRET</code>\n"
            "OKX: <code>/connect_exchange okx demo API_KEY API_SECRET PASSPHRASE</code>\n\n"
            "Send this only in a private chat. The credential message is deleted immediately. "
            "Enable Read + Trade only; never enable Withdraw.",
            parse_mode="HTML",
        )
        return
    _, exchange_raw, environment, api_key, api_secret, *extra = parts
    try:
        exchange = ExchangeName(exchange_raw.lower())
    except ValueError:
        await message.answer("⚠️ Unsupported exchange.")
        return
    if exchange not in {ExchangeName.BINGX, ExchangeName.OKX, ExchangeName.BYBIT, ExchangeName.BINANCE}:
        await message.answer("⚠️ Unsupported exchange.")
        return
    testnet = environment.lower() in {"demo", "testnet", "paper"}
    if not testnet and os.getenv("ALLOW_USER_LIVE_CONNECTIONS", "false").lower() not in {"1", "true", "yes", "on"}:
        await message.answer("⛔ Live account connections are locked. Use <code>demo</code>.", parse_mode="HTML")
        return
    passphrase = extra[0] if extra else ""
    if exchange is ExchangeName.OKX and not passphrase:
        await message.answer("⚠️ OKX requires an API passphrase.")
        return
    try:
        store = _credential_store()
        store.save(message.from_user.id, exchange, api_key, api_secret, testnet=testnet, passphrase=passphrase)
        user_registry = _user_registry(message.from_user.id, exchange)
        adapter = user_registry.create(exchange)
        try:
            health = await adapter.health()
        finally:
            await adapter.close()
        if not health.authenticated:
            store.delete(message.from_user.id, exchange)
            await message.answer(
                f"⛔ <b>{exchange.value.title()} connection rejected</b>\n"
                "Authentication failed. Verify the credential permissions and environment.\n\n"
                "Nothing was saved.",
                parse_mode="HTML",
            )
            return
    except ExchangeError as exc:
        await message.answer(f"⛔ {public_error_message(exc, context='EXCHANGE')}", parse_mode="HTML")
        return
    await message.answer(
        f"✅ <b>{exchange.value.title()} connected to your Telegram account</b>\n\n"
        f"Environment: <b>{'DEMO/TESTNET' if testnet else 'LIVE'}</b>\n"
        "Credentials are encrypted at rest and used only for your commands.",
        parse_mode="HTML",
    )


@router.message(Command("disconnect_exchange"))
async def disconnect_exchange(message: Message) -> None:
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer("Usage: <code>/disconnect_exchange bingx</code>", parse_mode="HTML")
        return
    try:
        exchange = ExchangeName(parts[1].lower())
        removed = _credential_store().delete(message.from_user.id, exchange)
    except (ValueError, ExchangeError) as exc:
        await message.answer(f"⚠️ {public_error_message(exc, context='EXCHANGE')}", parse_mode="HTML")
        return
    await message.answer("✅ Exchange disconnected." if removed else "ℹ️ That exchange was not connected.")


@router.message(Command("my_exchanges"))
async def my_exchanges(message: Message) -> None:
    try:
        connections = _credential_store().list(message.from_user.id)
    except ExchangeError as exc:
        await message.answer(f"⚠️ {public_error_message(exc, context='EXCHANGE')}", parse_mode="HTML")
        return
    if not connections:
        await message.answer("🔌 You have no connected exchanges. Use <code>/connect_exchange</code>.", parse_mode="HTML")
        return
    rows = [
        f"• <b>{name.value.upper()}</b> · {'DEMO/TESTNET' if testnet else 'LIVE'} · {escape(status)}"
        for name, testnet, status in connections
    ]
    await message.answer("🔐 <b>Your exchange accounts</b>\n\n" + "\n".join(rows), parse_mode="HTML")


@router.message(Command("exchanges"))
async def exchanges_status(message: Message) -> None:
    lines = [
        "🔌 <b>Exchange Foundation</b>", "",
        "v9.9.0 adds encrypted, isolated exchange accounts for every Telegram user.",
        "Every authenticated command uses only the credentials of the user who sent it.", "",
    ]
    connected = {item[0] for item in _credential_store().list(message.from_user.id)}
    for exchange in registry.available():
        if exchange in connected:
            health = await _user_adapter_call(message.from_user.id, exchange, "health")
        else:
            health = await _adapter_call(exchange, "health")
        environment = "TESTNET" if health.testnet else "PRODUCTION"
        latency = f" · {health.latency_ms:.0f} ms" if health.latency_ms is not None else ""
        default = " · DEFAULT" if exchange is _default_exchange() else ""
        lines.append(f"• <b>{exchange.value.upper()}</b> — {_STATUS_LABELS[health.status]} · {environment}{latency}{default}")
        if health.endpoint:
            lines.append(f"  Endpoint: <code>{escape(health.endpoint)}</code>")
        if health.error and health.error != "credentials_not_configured":
            lines.append("  <code>temporarily unavailable</code>")
    lines.extend([
        "", "<b>Commands</b>",
        "<code>/connect_exchange bingx demo API_KEY API_SECRET</code>",
        "<code>/disconnect_exchange bingx</code> · <code>/my_exchanges</code>",
        "<code>/exchange_balance [okx|bingx|bybit|binance]</code>",
        "<code>/exchange_positions [okx|bingx|bybit|binance]</code>",
        "<code>/exchange_orders [okx|bingx|bybit|binance] [SYMBOL]</code>",
        "<code>/exchange_symbol [okx|bingx|bybit|binance] BTCUSDT</code>",
        "<code>/exchange_account [okx|bingx]</code>",
        "<code>/exchange_safety</code>",
        "<code>/exchange_preflight [okx|bingx] BTCUSDT BUY 0.001 60000 3</code>",
        "<code>/demo_order bingx BTCUSDT BUY MARKET 0.001 60000 3</code>",
        "<code>/demo_cancel bingx BTCUSDT ORDER_ID</code>",
        "<code>/demo_status bingx BTCUSDT ORDER_ID</code>",
        "<code>/demo_kill</code> · <code>/demo_resume</code>", "",
        "🔒 User API secrets are encrypted in the database and isolated by Telegram user ID.",
    ])
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("exchange_balance"))
async def exchange_balance(message: Message) -> None:
    exchange, _ = _parse_exchange((message.text or "").split()[1:])
    try:
        balances = await _user_adapter_call(message.from_user.id, exchange, "balances")
    except ExchangeError as exc:
        await message.answer(f"⚠️ <b>{exchange.value.title()} balance unavailable</b>\n{public_error_message(exc, context='ACCOUNT')}", parse_mode="HTML")
        return
    rows = [f"• <b>{escape(i.asset)}</b> · wallet {_money(i.wallet_balance)} · available {_money(i.available_balance)}" for i in balances]
    await message.answer(f"💰 <b>{exchange.value.title()} balances</b>\n\n" + ("\n".join(rows) if rows else "No non-zero balances."), parse_mode="HTML")


@router.message(Command("exchange_positions"))
async def exchange_positions(message: Message) -> None:
    exchange, _ = _parse_exchange((message.text or "").split()[1:])
    try:
        positions = await _user_adapter_call(message.from_user.id, exchange, "positions")
    except ExchangeError as exc:
        await message.answer(f"⚠️ <b>{exchange.value.title()} positions unavailable</b>\n{public_error_message(exc, context='ACCOUNT')}", parse_mode="HTML")
        return
    rows = [f"• <b>{escape(i.symbol)} {i.side}</b> · qty {_money(i.quantity)} · entry {_money(i.entry_price)} · PnL {_money(i.unrealized_pnl)} · {i.leverage}x" for i in positions]
    await message.answer(f"📌 <b>{exchange.value.title()} positions</b>\n\n" + ("\n".join(rows) if rows else "No open positions."), parse_mode="HTML")


@router.message(Command("exchange_orders"))
async def exchange_orders(message: Message) -> None:
    exchange, args = _parse_exchange((message.text or "").split()[1:])
    symbol = args[0].upper() if args else None
    try:
        orders = await _user_adapter_call(message.from_user.id, exchange, "open_orders", symbol)
    except ExchangeError as exc:
        await message.answer(f"⚠️ <b>{exchange.value.title()} orders unavailable</b>\n{public_error_message(exc, context='ACCOUNT')}", parse_mode="HTML")
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
        await message.answer(f"⚠️ <b>{exchange.value.title()} symbol rules unavailable</b>\n{public_error_message(exc, context='EXCHANGE')}", parse_mode="HTML")
        return
    minimum = _money(rules.min_notional) if rules.min_notional is not None else "not published"
    await message.answer(
        f"⚙️ <b>{exchange.value.title()} · {escape(rules.symbol)} execution rules</b>\n\n"
        f"Status: <b>{escape(rules.status)}</b>\nPair: <b>{escape(rules.base_asset)}/{escape(rules.quote_asset)}</b>\n"
        f"Price tick: <code>{_money(rules.price_tick)}</code>\nQuantity step: <code>{_money(rules.quantity_step)}</code>\n"
        f"Minimum quantity: <code>{_money(rules.min_quantity)}</code>\nMinimum notional: <code>{minimum}</code>", parse_mode="HTML")


@router.message(Command("exchange_account"))
async def exchange_account(message: Message) -> None:
    exchange, args = _parse_exchange((message.text or "").split()[1:])
    symbol = args[0].upper() if args else None
    try:
        user_registry = _user_registry(message.from_user.id, exchange)
        user_manager = ExchangeManager(user_registry, operation_timeout_seconds=float(os.getenv("EXCHANGE_OPERATION_TIMEOUT", "25")))
        snapshot = await user_manager.snapshot(exchange, symbol=symbol)
    except ExchangeError as exc:
        await message.answer(
            f"⚠️ <b>{exchange.value.title()} authenticated snapshot unavailable</b>\n"
            f"{public_error_message(exc, context='ACCOUNT')}\n\n"
            "Add read-only API credentials in Render. Do not enable withdrawal permissions.",
            parse_mode="HTML",
        )
        return

    total_equity = sum((item.wallet_balance for item in snapshot.balances), Decimal("0"))
    total_available = sum((item.available_balance for item in snapshot.balances), Decimal("0"))
    environment = "DEMO/TESTNET" if snapshot.health.testnet else "LIVE ACCOUNT"
    lines = [
        f"🔐 <b>{exchange.value.title()} authenticated account</b>", "",
        f"Environment: <b>{environment}</b>",
        f"Assets: <b>{snapshot.non_zero_assets}</b>",
        f"Wallet total*: <code>{_money(total_equity)}</code>",
        f"Available total*: <code>{_money(total_available)}</code>",
        f"Open positions: <b>{snapshot.open_position_count}</b>",
        f"Open orders: <b>{snapshot.open_order_count}</b>", "",
        "<i>*Raw exchange asset values are summed without FX conversion.</i>",
        "🔒 Read-only snapshot. No order action was performed.",
    ]
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("exchange_safety"))
async def exchange_safety(message: Message) -> None:
    policy = ExecutionSafetyPolicy.from_env()
    symbols = ", ".join(sorted(policy.allowed_symbols)) or "none"
    live = "UNLOCKED" if policy.live_enabled else "LOCKED"
    await message.answer(
        "🛡 <b>Execution Safety Core</b>\n\n"
        f"Live execution: <b>{live}</b>\n"
        f"Demo required: <b>{'YES' if policy.require_demo else 'NO'}</b>\n"
        f"Maximum notional: <code>{_money(policy.max_notional_usdt)} USDT</code>\n"
        f"Maximum leverage: <code>{policy.max_leverage}x</code>\n"
        f"Maximum open positions: <code>{policy.max_open_positions}</code>\n"
        f"Allowed symbols: <code>{escape(symbols)}</code>\n\n"
        f"Demo execution: <b>{'ENABLED' if execution_manager.enabled else 'LOCKED'}</b>\n"
        "Live execution remains unavailable.",
        parse_mode="HTML",
    )


@router.message(Command("exchange_preflight"))
async def exchange_preflight(message: Message) -> None:
    exchange, args = _parse_exchange((message.text or "").split()[1:])
    if len(args) < 5:
        await message.answer(
            "Usage: <code>/exchange_preflight [okx|bingx] BTCUSDT BUY 0.001 60000 3</code>",
            parse_mode="HTML",
        )
        return
    symbol, side_raw, quantity_raw, price_raw, leverage_raw = args[:5]
    try:
        side = OrderSide(side_raw.upper())
        quantity = Decimal(quantity_raw)
        price = Decimal(price_raw)
        leverage = int(leverage_raw)
    except (ValueError, ArithmeticError):
        await message.answer("⚠️ Invalid side, quantity, price, or leverage.")
        return

    try:
        user_registry = _user_registry(message.from_user.id, exchange)
        adapter = user_registry.create(exchange)
    except ExchangeConfigurationError:
        adapter = registry.create(exchange)
    try:
        rules = await adapter.symbol_rules(symbol)
    except ExchangeError as exc:
        await message.answer(
            f"⚠️ <b>{exchange.value.title()} preflight unavailable</b>\n{public_error_message(exc, context='EXCHANGE')}",
            parse_mode="HTML",
        )
        return
    finally:
        await adapter.close()

    positions = ()
    orders = ()
    portfolio_note = "Public-only preflight; portfolio duplicate/position checks were not available."
    try:
        user_registry = _user_registry(message.from_user.id, exchange)
        user_manager = ExchangeManager(user_registry, operation_timeout_seconds=float(os.getenv("EXCHANGE_OPERATION_TIMEOUT", "25")))
        snapshot = await user_manager.snapshot(exchange, symbol=symbol)
    except (ExchangeConfigurationError, ExchangeError):
        pass
    else:
        positions = snapshot.positions
        orders = snapshot.open_orders
        portfolio_note = "Authenticated portfolio state included."

    policy = ExecutionSafetyPolicy.from_env()
    intent = OrderIntent(
        exchange=exchange,
        symbol=symbol,
        side=side,
        quantity=quantity,
        reference_price=price,
        leverage=leverage,
        demo=not (os.getenv(f"{exchange.value.upper()}_DEMO", "true").strip().lower() in {"0", "false", "no", "off"}),
    )
    decision = ExecutionSafetyValidator(policy).validate(
        intent, rules, positions=positions, open_orders=orders
    )
    verdict = "✅ APPROVED BY PREFLIGHT" if decision.approved else "⛔ REJECTED BY PREFLIGHT"
    violations = "\n".join(f"• <code>{escape(item)}</code>" for item in decision.violations) or "• none"
    warnings = "\n".join(f"• <code>{escape(item)}</code>" for item in decision.warnings) or "• none"
    await message.answer(
        f"🧪 <b>{exchange.value.title()} execution preflight</b>\n\n"
        f"Verdict: <b>{verdict}</b>\n"
        f"Symbol: <code>{escape(decision.normalized_symbol)}</code>\n"
        f"Side: <code>{side.value}</code>\n"
        f"Quantity: <code>{_money(quantity)}</code>\n"
        f"Reference price: <code>{_money(price)}</code>\n"
        f"Notional: <code>{_money(decision.notional)} USDT</code>\n"
        f"Leverage: <code>{leverage}x</code>\n\n"
        f"<b>Violations</b>\n{violations}\n\n"
        f"<b>Warnings</b>\n{warnings}\n\n"
        f"{escape(portfolio_note)}\n"
        "🔒 Validation only. No order was sent.",
        parse_mode="HTML",
    )


@router.message(Command("demo_order"))
async def demo_order(message: Message) -> None:
    exchange, args = _parse_exchange((message.text or "").split()[1:])
    if exchange is not ExchangeName.BINGX or len(args) < 6:
        await message.answer(
            "Usage: <code>/demo_order bingx BTCUSDT BUY MARKET 0.001 60000 3</code>\n"
            "Limit: <code>/demo_order bingx BTCUSDT BUY LIMIT 0.001 60000 3 59000</code>",
            parse_mode="HTML",
        )
        return
    symbol, side_raw, order_type, quantity_raw, reference_raw, leverage_raw, *extra = args
    try:
        side = OrderSide(side_raw.upper())
        quantity = Decimal(quantity_raw)
        reference_price = Decimal(reference_raw)
        leverage = int(leverage_raw)
        limit_price = Decimal(extra[0]) if order_type.upper() == "LIMIT" and extra else None
    except (ValueError, ArithmeticError):
        await message.answer("⚠️ Invalid demo order arguments.")
        return
    if order_type.upper() not in {"MARKET", "LIMIT"} or (order_type.upper() == "LIMIT" and limit_price is None):
        await message.answer("⚠️ Supported types: MARKET or LIMIT; LIMIT requires a final price.")
        return
    try:
        user_execution = _user_execution_manager(message.from_user.id, exchange)
        receipt = await user_execution.submit(DemoOrderRequest(
            exchange=exchange, symbol=symbol, side=side, order_type=order_type.upper(),
            quantity=quantity, reference_price=reference_price, leverage=leverage,
            limit_price=limit_price,
        ))
    except ExchangeError as exc:
        await message.answer(f"⛔ <b>Demo execution failed</b>\n{public_error_message(exc, context='EXCHANGE')}", parse_mode="HTML")
        return
    if not receipt.order:
        violations = "\n".join(f"• <code>{escape(v)}</code>" for v in receipt.violations) or "• none"
        await message.answer(f"⛔ <b>Demo order rejected</b>\n\n{violations}", parse_mode="HTML")
        return
    order = receipt.order
    await message.answer(
        f"✅ <b>BingX demo order accepted automatically</b>\n\n"
        f"Order ID: <code>{escape(order.order_id or 'pending')}</code>\n"
        f"Client ID: <code>{escape(receipt.client_order_id)}</code>\n"
        f"{escape(order.symbol or symbol)} · {escape(order.side or side.value)} · {escape(order.order_type or order_type.upper())}\n"
        f"Quantity: <code>{_money(order.quantity or quantity)}</code>\n"
        f"Status: <b>{escape(order.status)}</b>\n"
        f"Latency: <code>{receipt.latency_ms or 0} ms</code>\n\n"
        "🔒 Demo account only. No manual confirmation was required.", parse_mode="HTML"
    )


@router.message(Command("demo_cancel"))
async def demo_cancel(message: Message) -> None:
    exchange, args = _parse_exchange((message.text or "").split()[1:])
    if exchange is not ExchangeName.BINGX or len(args) < 2:
        await message.answer("Usage: <code>/demo_cancel bingx BTCUSDT ORDER_ID</code>", parse_mode="HTML")
        return
    try:
        order = await _user_execution_manager(message.from_user.id, exchange).cancel(exchange, args[0], args[1])
    except ExchangeError as exc:
        await message.answer(f"⛔ {public_error_message(exc, context='EXCHANGE')}", parse_mode="HTML")
        return
    await message.answer(f"🛑 Demo order <code>{escape(order.order_id)}</code>: <b>{escape(order.status)}</b>", parse_mode="HTML")


@router.message(Command("demo_status"))
async def demo_status(message: Message) -> None:
    exchange, args = _parse_exchange((message.text or "").split()[1:])
    if exchange is not ExchangeName.BINGX or len(args) < 2:
        await message.answer("Usage: <code>/demo_status bingx BTCUSDT ORDER_ID</code>", parse_mode="HTML")
        return
    try:
        order = await _user_execution_manager(message.from_user.id, exchange).status(exchange, args[0], args[1])
    except ExchangeError as exc:
        await message.answer(f"⛔ {public_error_message(exc, context='EXCHANGE')}", parse_mode="HTML")
        return
    await message.answer(
        f"📍 <b>Demo order status</b>\n\nID: <code>{escape(order.order_id)}</code>\n"
        f"Status: <b>{escape(order.status)}</b>\nFilled: <code>{_money(order.executed_quantity)}/{_money(order.quantity)}</code>",
        parse_mode="HTML",
    )


@router.message(Command("demo_kill"))
async def demo_kill(message: Message) -> None:
    os.environ[f"USER_EXECUTION_KILLED_{message.from_user.id}"] = "true"
    await message.answer("🛑 <b>Your execution is disabled for this runtime.</b>", parse_mode="HTML")


@router.message(Command("demo_resume"))
async def demo_resume(message: Message) -> None:
    os.environ.pop(f"USER_EXECUTION_KILLED_{message.from_user.id}", None)
    await message.answer(
        "▶️ Runtime kill switch released. Environment policy still applies.", parse_mode="HTML"
    )
