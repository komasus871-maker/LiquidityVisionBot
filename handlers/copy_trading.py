from __future__ import annotations

import json
import os
from html import escape

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from services.copy_trading import CopyTradingService
from services.copy_training import CopyTrainingService
from services.copy_execution_intelligence import CopyExecutionIntelligenceService
from services.copy_guardrail_outcomes import CopyGuardrailOutcomeService
from services.copy_similarity import CopySimilarityService
from services.operator_authorization import OperatorAuthorizationService, OperatorCapability
from services.paper_copy_analytics import PaperCopyAnalyticsService
from services.capabilities import CapabilityService
from services.public_errors import public_error_message

router = Router()
service = CopyTradingService()
training_service = CopyTrainingService()
intelligence_service = CopyExecutionIntelligenceService()
outcome_service = CopyGuardrailOutcomeService()
similarity_service = CopySimilarityService()
operators = OperatorAuthorizationService()
paper_analytics = PaperCopyAnalyticsService()
capabilities = CapabilityService()


def _status_text(profile: dict, stats: dict, *, include_diagnostics: bool = True) -> str:
    def values(name: str) -> str:
        try:
            parsed = json.loads(profile.get(name) or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = []
        return ", ".join(escape(str(item)) for item in parsed) or "all"

    global_on = os.getenv("COPY_EXECUTION_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
    active = bool(profile.get("enabled") and profile.get("auto_copy") and global_on)
    sizing = str(profile.get("sizing_mode") or "RISK_PERCENT")
    sizing_value = (
        f"${float(profile.get('fixed_usdt') or 0):,.2f}" if sizing == "FIXED_USDT" else
        f"{float(profile.get('equity_pct') or 0):.2f}% equity" if sizing == "EQUITY_PERCENT" else
        f"{float(profile.get('copy_multiplier') or 0):.2f}x source" if sizing == "COPY_MULTIPLIER" else
        f"{float(profile.get('risk_pct') or 0):.2f}% risk"
    )
    expectancy = stats.get("strategy_expectancy_r")
    profit_factor = stats.get("strategy_profit_factor")
    diagnostics = ""
    if include_diagnostics:
        diagnostics = f"""

<b>Operator reconciliation diagnostics</b>
Legacy confirmed open: {int(stats.get('legacy_confirmed_open') or stats.get('reconciliation_confirmed_active_legacy_count') or 0)}
Confirmed legacy active: {int(stats.get('legacy_confirmed_open') or stats.get('reconciliation_confirmed_active_legacy_count') or 0)}
Unified open: {int(stats.get('unified_open_positions') or stats.get('reconciliation_unified_open_count') or 0)}
Hybrid open: {int(stats.get('hybrid_open_positions') or 0)}
Accounting authority: <b>{escape(str(stats.get('accounting_authority') or 'LEGACY'))}</b>
Unified symbols: {escape(', '.join(stats.get('unified_symbols') or ()) or 'none')}
Unresolved legacy: {int(stats.get('reconciliation_unresolved_legacy_count') or 0)} ({float(stats.get('reconciliation_unresolved_heat_r') or 0):.2f}R)
Heat source: <b>{escape(str(stats.get('reconciliation_heat_source') or 'UNKNOWN'))}</b>
Portfolio state: <b>{escape(str(stats.get('reconciliation_status') or 'UNKNOWN'))}</b>
Mismatch: <b>{'YES' if stats.get('reconciliation_mismatch_detected') else 'NO'}</b>
<i>Legacy values are shadow/rollback diagnostics.</i>"""
    return f"""📋 <b>Copy Trading</b>

Automatic PAPER copy: <b>{'ACTIVE' if active else 'PAUSED'}</b>
Profile: <b>{escape(str(profile.get('profile_name') or 'STANDARD'))}</b>
Sizing: <b>{escape(sizing)} · {sizing_value}</b>
Paper balance / equity: <b>${float(profile['paper_balance']):,.2f} / ${float(stats.get('equity') or profile['paper_balance']):,.2f}</b>

<b>Limits</b>
Positions / heat: <b>{int(profile['max_positions'])} / {float(profile['max_heat_r']):.2f}R</b>
Daily loss / slippage: <b>{float(profile['daily_loss_pct']):.2f}% / {float(profile['max_slippage_pct']):.2f}%</b>
Order / portfolio exposure: <b>{float(profile.get('max_notional_pct') or 0):.0f}% / {float(profile.get('max_portfolio_exposure_pct') or 0):.0f}%</b>
Confidence / cooldown: <b>{float(profile.get('min_confidence') or 0):.0f}% / {int(profile.get('symbol_cooldown_min') or 0)} min</b>

<b>Filters</b>
Symbols: <b>{escape(str(profile.get('symbol_policy') or 'ALL'))}</b> · allow {values('symbol_whitelist_json')} · block {values('symbol_blacklist_json')}
Timeframes: <b>{values('timeframe_filters_json')}</b>
Setups: <b>{values('setup_filters_json')}</b>
Directions: <b>{values('direction_filters_json')}</b>
Experimental: <b>{'ON' if profile.get('allow_experimental') else 'OFF'}</b>

<b>Current PAPER portfolio</b>
Open positions: <b>{int(stats.get('unified_open_positions') or 0)}</b>
Gross exposure / heat: <b>${float(stats.get('unified_gross_notional') or 0):,.2f} / {float(stats.get('unified_confirmed_heat_r') or 0):.2f}R</b>
Today / net equity: <b>${float(stats.get('daily_pnl') or 0):+,.2f} / ${float(stats.get('unified_equity') or profile['paper_balance']):,.2f}</b>

<b>Performance separation</b>
Eligible / accepted / rejected: <b>{int(stats.get('policy_eligible') or 0)} / {int(stats.get('policy_accepted') or 0)} / {int(stats.get('policy_rejected') or 0)}</b>
Pure strategy W/L/BE: <b>{int(stats.get('strategy_wins') or 0)}/{int(stats.get('strategy_losses') or 0)}/{int(stats.get('strategy_breakeven') or 0)}</b>
Expectancy / profit factor: <b>{'insufficient data' if expectancy is None else f'{float(expectancy):+.2f}R'} / {'insufficient data' if profit_factor is None else f'{float(profit_factor):.2f}'}</b>
Actual net PnL / fees: <b>${float(stats.get('actual_net_pnl') or 0):+,.2f} / ${float(stats.get('actual_fees') or 0):,.2f}</b>
Manual or panic closes: <b>{int(stats.get('manual_intervention_count') or 0)}</b> (kept out of pure strategy statistics)

<code>/copy_profile</code> · <code>/copy_size</code> · <code>/copy_symbols</code> · <code>/copy_filters</code>
<code>/copy_queue</code> · <code>/orders</code> · <code>/fills</code> · <code>/positions</code>
<code>/copy_enable</code> starts automatic PAPER copy · <code>/copy_disable</code> pauses entries · <code>/panic</code> closes only your PAPER positions.

LIVE remains fail-closed unless separately certified; AI has no execution authority.{diagnostics}"""


@router.message(Command("copy"))
async def copy_status(message: Message):
    profile = service.ensure_profile(message.from_user.id)
    await message.answer(
        _status_text(profile, service.profile_stats(message.from_user.id), include_diagnostics=False),
        parse_mode="HTML",
    )


@router.message(Command("copy_performance"))
async def copy_performance(message: Message):
    stats = service.performance_stats(message.from_user.id)
    cohorts = paper_analytics.report(message.from_user.id, days=90)
    n = int(stats["strategy_closed"])
    expectancy = stats["strategy_expectancy_r"]
    pf = stats["strategy_profit_factor"]
    average_win = stats["strategy_average_win_r"]
    average_loss = stats["strategy_average_loss_r"]
    average_win_text = "n/a" if average_win is None else f"{average_win:+.2f}R"
    average_loss_text = "n/a" if average_loss is None else f"{average_loss:+.2f}R"
    evidence = "INSUFFICIENT_SAMPLE" if n < 30 else "DESCRIPTIVE_SAMPLE"
    await message.answer(
        "<b>PAPER Copy · Performance Separation</b>\n\n"
        f"Candidates / accepted / rejected: <b>{stats['policy_eligible']} / {stats['policy_accepted']} / {stats['policy_rejected']}</b>\n"
        f"Opened / closed: <b>{stats['execution_opened']} / {stats['execution_closed']}</b>\n"
        f"Pure strategy W/L/BE: <b>{stats['strategy_wins']}/{stats['strategy_losses']}/{stats['strategy_breakeven']}</b>\n"
        f"Expectancy / PF: <b>{'n/a' if expectancy is None else f'{expectancy:+.2f}R'} / {'n/a' if pf is None else f'{pf:.2f}'}</b>\n"
        f"Average win / loss: <b>{average_win_text} / {average_loss_text}</b>\n"
        f"Gross PnL / fees / net: <b>${stats['actual_gross_pnl']:+,.2f} / ${stats['actual_fees']:,.2f} / ${stats['actual_net_pnl']:+,.2f}</b>\n"
        f"Average slippage: <b>{stats['actual_average_slippage_pct']:.4f}%</b>\n"
        f"Evidence state: <code>{evidence}</code>\n\n"
        f"Analytics V2 resolved/executed/counterfactual: <b>{cohorts['resolved']} / {cohorts['executed']} / {cohorts['counterfactual']}</b>\n"
        "Cohorts: <code>/copy_analytics</code> · "
        "Admission outcomes: <code>/copy_rejections</code> · rejected-signal outcomes: <code>/copy_guardrails</code>\n"
        "PAPER results are descriptive and do not establish future profitability.")


@router.message(Command("copy_analytics"))
async def copy_analytics(message: Message):
    if not capabilities.has(message.from_user.id, "ADVANCED_COHORT_ANALYTICS"):
        await message.answer(capabilities.preview("ADVANCED_COHORT_ANALYTICS", message.from_user.id))
        return
    report = paper_analytics.report(message.from_user.id, days=90)

    def top(items) -> str:
        return "\n".join(f"• <code>{escape(str(item['key']))}</code> · n={item['sample']} · "
                         f"{item['expectancy_r']:+.2f}R expectancy · {item['net_r']:+.2f}R net"
                         for item in items[:5]) or "• insufficient resolved sample"

    guardrails = report["guardrail_counterfactuals"]
    counterfactual = "\n".join(
        f"• <code>{code}</code> · n={item['sample']} · avoided {item['avoided_losses']} losses · "
        f"missed {item['missed_wins']} wins · {item['net_shadow_r']:+.2f}R shadow"
        for code, item in guardrails.items()
    )
    await message.answer(
        f"<b>PAPER Copy Analytics V2 · 90 days</b>\n\n"
        f"Resolved: <b>{report['resolved']}</b> · executed {report['executed']} · counterfactual {report['counterfactual']}\n\n"
        f"<b>Strategy</b>\n{top(report['by_strategy'])}\n\n"
        f"<b>Timeframe</b>\n{top(report['by_timeframe'])}\n\n"
        f"<b>Symbol</b>\n{top(report['by_symbol'])}\n\n"
        f"<b>Decision-time Quality</b>\n{top(report['by_quality'])}\n\n"
        f"<b>Decision-time Entry Readiness</b>\n{top(report['by_readiness'])}\n\n"
        f"<b>Guardrail counterfactuals</b>\n{counterfactual}\n\n"
        "Descriptive PAPER research only. Counterfactuals never weaken guardrails or change execution policy automatically.")


def _control_values(parts: list[str]) -> list[str]:
    return [value.strip() for chunk in parts for value in chunk.split(",") if value.strip()]


@router.message(Command("copy_profile"))
async def copy_profile(message: Message):
    parts = (message.text or "").split()
    if len(parts) == 1:
        profile = service.ensure_profile(message.from_user.id)
        await message.answer(
            f"Current copy profile: <b>{escape(str(profile.get('profile_name') or 'STANDARD'))}</b>\n"
            "Use <code>/copy_profile conservative|standard|aggressive|custom</code>. "
            "Any limit or filter override automatically marks the profile CUSTOM.", parse_mode="HTML")
        return
    try:
        profile = service.select_profile(message.from_user.id, parts[1])
    except ValueError as exc:
        await message.answer(escape(str(exc)), parse_mode="HTML")
        return
    await message.answer(
        f"✅ Profile set to <b>{escape(str(profile['profile_name']))}</b>. "
        "This changes PAPER risk defaults only; it does not enable copying.", parse_mode="HTML")


@router.message(Command("copy_symbols"))
async def copy_symbols(message: Message):
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer(
            "Use <code>/copy_symbols all</code>, <code>/copy_symbols whitelist BTCUSDT,ETHUSDT</code>, "
            "<code>/copy_symbols blacklist DOGEUSDT</code>, or <code>/copy_symbols clear</code>.",
            parse_mode="HTML")
        return
    action = parts[1].lower()
    values = _control_values(parts[2:])
    try:
        if action == "all":
            profile = service.update_profile(message.from_user.id, symbol_policy="ALL")
        elif action == "whitelist" and values:
            profile = service.update_profile(message.from_user.id, symbol_policy="WHITELIST",
                                             symbol_whitelist_json=values)
        elif action == "blacklist" and values:
            profile = service.update_profile(message.from_user.id, symbol_blacklist_json=values)
        elif action == "clear":
            profile = service.update_profile(message.from_user.id, symbol_policy="ALL",
                                             symbol_whitelist_json=[], symbol_blacklist_json=[])
        else:
            raise ValueError("Symbol command requires a supported action and at least one symbol")
    except ValueError as exc:
        await message.answer(escape(str(exc)), parse_mode="HTML")
        return
    await message.answer(
        f"✅ Symbol controls updated. Policy: <b>{escape(str(profile['symbol_policy']))}</b>. "
        "Symbols are canonicalized, so BTC-USDT and BTCUSDT cannot bypass the same rule.", parse_mode="HTML")


@router.message(Command("copy_filters"))
async def copy_filters(message: Message):
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer(
            "Use <code>/copy_filters timeframe 5m,15m</code>, <code>/copy_filters setup breakout</code>, "
            "<code>/copy_filters direction long,short</code>, <code>/copy_filters experimental on|off</code>, "
            "or <code>/copy_filters clear</code>.", parse_mode="HTML")
        return
    action, values = parts[1].lower(), _control_values(parts[2:])
    try:
        if action == "clear":
            profile = service.update_profile(message.from_user.id, timeframe_filters_json=[],
                setup_filters_json=[], direction_filters_json=[], allow_experimental=0)
        elif action == "timeframe" and values:
            profile = service.update_profile(message.from_user.id, timeframe_filters_json=values)
        elif action == "setup" and values:
            profile = service.update_profile(message.from_user.id, setup_filters_json=values)
        elif action == "direction" and values:
            profile = service.update_profile(message.from_user.id, direction_filters_json=values)
        elif action == "experimental" and len(values) == 1 and values[0].lower() in {"on", "off"}:
            profile = service.update_profile(message.from_user.id,
                                             allow_experimental=int(values[0].lower() == "on"))
        else:
            raise ValueError("Invalid copy filter command")
    except ValueError as exc:
        await message.answer(escape(str(exc)), parse_mode="HTML")
        return
    await message.answer(
        f"✅ Copy filters updated under <b>{escape(str(profile['profile_name']))}</b>. "
        "Filtered signals cannot enter the execution queue.", parse_mode="HTML")


@router.message(Command("copy_enable"))
async def copy_enable(message: Message):
    service.update_profile(message.from_user.id, enabled=1, auto_copy=1)
    await message.answer("🟢 <b>Automatic PAPER copy is active.</b> New eligible signals now pass through your filters, risk limits, durable queue, order, fill, and position lifecycle.", parse_mode="HTML")


@router.message(Command("copy_disable"))
async def copy_disable(message: Message):
    service.update_profile(message.from_user.id, enabled=0, auto_copy=0)
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


@router.message(Command("copy_size"))
async def copy_size(message: Message):
    parts = (message.text or "").split()
    try:
        mode = parts[1].lower()
        if mode == "risk":
            profile = service.update_profile(message.from_user.id, sizing_mode="RISK_PERCENT")
            await message.answer(f"✅ Sizing mode set to <b>Risk %</b> ({float(profile['risk_pct']):.2f}%).", parse_mode="HTML")
            return
        if mode == "fixed":
            value = float(parts[2].replace(",", "."))
            profile = service.update_profile(message.from_user.id, sizing_mode="FIXED_USDT", fixed_usdt=value)
            await message.answer(f"✅ Sizing mode set to <b>Fixed USDT</b>: <b>${float(profile['fixed_usdt']):,.2f}</b>.", parse_mode="HTML")
            return
        if mode == "equity":
            value = float(parts[2].replace(",", "."))
            profile = service.update_profile(message.from_user.id, sizing_mode="EQUITY_PERCENT",
                                             equity_pct=value)
            await message.answer(f"✅ Sizing mode set to <b>{float(profile['equity_pct']):.2f}% of equity</b>.", parse_mode="HTML")
            return
        if mode == "multiplier":
            value = float(parts[2].replace(",", "."))
            profile = service.update_profile(message.from_user.id, sizing_mode="COPY_MULTIPLIER",
                                             copy_multiplier=value)
            await message.answer(f"✅ Proportional sizing set to <b>{float(profile['copy_multiplier']):.2f}x</b>. Signals without trusted source size fail closed.", parse_mode="HTML")
            return
        raise ValueError
    except (IndexError, ValueError):
        await message.answer("Use <code>/copy_size risk</code>, <code>/copy_size fixed 100</code>, <code>/copy_size equity 10</code>, or <code>/copy_size multiplier 0.5</code>.", parse_mode="HTML")


@router.message(Command("copy_leverage"))
async def copy_leverage(message: Message):
    try:
        value = int((message.text or "").split(maxsplit=1)[1])
        profile = service.update_profile(message.from_user.id, leverage=value)
    except (IndexError, ValueError):
        await message.answer("Usage: <code>/copy_leverage 3</code>\nAllowed: 1–125x (exchange limits still apply).", parse_mode="HTML")
        return
    await message.answer(f"✅ Copy execution leverage set to <b>{int(profile['leverage'])}x</b>.", parse_mode="HTML")


@router.message(Command("copy_auto"))
async def copy_auto(message: Message):
    try:
        raw = (message.text or "").split(maxsplit=1)[1].strip().lower()
        if raw not in {"on", "off"}:
            raise ValueError
    except (IndexError, ValueError):
        await message.answer("Usage: <code>/copy_auto on</code> or <code>/copy_auto off</code>", parse_mode="HTML")
        return
    enabled = int(raw == "on")
    service.update_profile(message.from_user.id, auto_copy=enabled, enabled=enabled)
    note = "automatic PAPER execution is active" if enabled else "new PAPER entries are paused"
    await message.answer(f"✅ Auto Copy is <b>{raw.upper()}</b>: {note}. LIVE remains fail-closed.", parse_mode="HTML")


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
        exposure = float(parts[4]) if len(parts) > 4 else None
        if not (1 <= positions <= 20 and .25 <= heat <= 20 and 0.1 <= daily <= 25 and
                (exposure is None or 1 <= exposure <= 500)):
            raise ValueError
    except (IndexError, ValueError):
        await message.answer("Usage: <code>/copy_limits 3 2.5 2 70</code>\n(max positions, heat R, daily loss %, optional portfolio exposure %)", parse_mode="HTML")
        return
    updates = {"max_positions": positions, "max_heat_r": heat, "daily_loss_pct": daily}
    if exposure is not None:
        updates["max_portfolio_exposure_pct"] = exposure
    service.update_profile(message.from_user.id, **updates)
    await message.answer("✅ Copy risk limits updated.", parse_mode="HTML")


@router.message(Command("copy_stats"))
async def copy_stats(message: Message):
    profile = service.ensure_profile(message.from_user.id)
    await message.answer(
        _status_text(profile, service.profile_stats(message.from_user.id), include_diagnostics=False),
        parse_mode="HTML",
    )


@router.message(Command("copy_diagnostics"))
async def copy_diagnostics(message: Message):
    actor = message.from_user.id if message.from_user else None
    if not operators.authorize(actor_telegram_id=actor, capability=OperatorCapability.SYSTEM_ADMIN,
                               action="COPY_DIAGNOSTICS_VIEW"):
        await message.answer("Operator diagnostics are restricted.")
        return
    profile = service.ensure_profile(message.from_user.id)
    await message.answer(
        _status_text(profile, service.profile_stats(message.from_user.id), include_diagnostics=True),
        parse_mode="HTML",
    )


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


@router.message(Command("copy_training"))
async def copy_training(message: Message):
    report = training_service.report(message.from_user.id)
    readiness = "🟢 READY" if report["learning_ready"] else "🟡 COLLECTING DATA"

    def render(items: list[dict]) -> str:
        if not items:
            return "No cohort has at least 3 closed executions yet."
        return "\n".join(
            f"• {item['cohort']} — {item['sample_size']} trades · "
            f"{item['average_r']:+.2f}R avg · {item['win_rate']:.0f}% WR"
            for item in items
        )

    text = f"""🧠 <b>Copy Training</b>

State: <b>{readiness}</b>
Closed sample: <b>{report['sample_size']}</b>
Win rate: <b>{report['win_rate']:.1f}%</b>
Average: <b>{report['average_r']:+.2f}R</b>
Total: <b>{report['total_r']:+.2f}R</b>

🏆 <b>Best cohorts</b>
{render(report['best_cohorts'])}

⚠️ <b>Weakest cohorts</b>
{render(report['weakest_cohorts'])}

The adaptive policy starts after 8 closed paper executions and can block a persistently negative cohort only after 15+ samples. Open and rejected positions are never used for training."""
    await message.answer(text, parse_mode="HTML")


@router.message(Command("copy_rejections"))
async def copy_rejections(message: Message):
    report = intelligence_service.report(message.from_user.id)

    def render_buckets(items) -> str:
        if not items:
            return "• No data"
        return "\n".join(
            f"• <code>{item.key}</code> — {item.count} · {item.share_pct:.1f}%"
            for item in items
        )

    if report["attempts"] == 0:
        await message.answer(
            "🔎 <b>Copy Execution Intelligence</b>\n\nNo execution attempts were recorded in the last 30 days.",
            parse_mode="HTML",
        )
        return

    recent = report["recent"]
    recent_text = "\n".join(
        f"• #{row['signal_id']} {row['symbol']} {row['side']} · <code>{row.get('rejection_code') or 'UNKNOWN'}</code>"
        for row in recent
    ) or "• No rejected executions"

    text = f"""🔎 <b>Copy Execution Intelligence</b>

Window: <b>{report['days']} days</b>
Attempts: <b>{report['attempts']}</b>
Accepted: <b>{report['accepted']}</b>
Rejected: <b>{report['rejected']}</b>
Acceptance rate: <b>{report['acceptance_rate']:.1f}%</b>

🚧 <b>Rejection reasons</b>
{render_buckets(report['by_code'])}

🪙 <b>Most rejected symbols</b>
{render_buckets(report['by_symbol'])}

⏱ <b>Most rejected timeframes</b>
{render_buckets(report['by_timeframe'])}

🕘 <b>Recent rejected attempts</b>
{recent_text}

This report is diagnostic only. Guardrails are never weakened automatically from rejection volume."""
    await message.answer(text, parse_mode="HTML")


@router.message(Command("copy_guardrails"))
async def copy_guardrails(message: Message):
    report = outcome_service.report(message.from_user.id)
    if report["resolved"] == 0:
        await message.answer(
            "🛡 <b>Guardrail Outcome Intelligence</b>\n\n"
            "No rejected signal has reached a terminal lifecycle state yet. "
            "The report will populate automatically as rejected signals close.",
            parse_mode="HTML",
        )
        return

    codes = "\n".join(
        f"• <code>{item.code}</code> — {item.resolved} resolved · "
        f"{item.avoided_losses} losses avoided · {item.missed_wins} wins missed · "
        f"{item.net_shadow_r:+.2f}R shadow"
        for item in report["by_code"]
    ) or "• No data"
    recent = "\n".join(
        f"• #{row['signal_id']} {row['symbol']} {row['side']} · "
        f"<code>{row.get('rejection_code') or 'UNKNOWN'}</code> · "
        f"{float(row.get('shadow_realized_r') or 0):+.2f}R"
        for row in report["recent"]
    ) or "• No data"
    text = f"""🛡 <b>Guardrail Outcome Intelligence</b>

Window: <b>{report['days']} days</b>
Resolved rejected signals: <b>{report['resolved']}</b>
Losses avoided: <b>{report['avoided_losses']}</b>
Profitable trades missed: <b>{report['missed_wins']}</b>
Counterfactual net: <b>{report['net_shadow_r']:+.2f}R</b>
Counterfactual average: <b>{report['average_shadow_r']:+.2f}R</b>

🧱 <b>Guardrail value by reason</b>
{codes}

🕘 <b>Recently resolved rejections</b>
{recent}

Shadow outcomes are diagnostic only. They never modify equity, realized PnL, or live risk limits."""
    await message.answer(text, parse_mode="HTML")


def _render_feature_list(items: list[str]) -> str:
    return ", ".join(escape(item) for item in items) if items else "—"


def _render_genome_value(value: object) -> str:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return escape(str(value))


@router.message(Command("copy_similar"))
async def copy_similar(message: Message):
    parts = (message.text or "").split()
    try:
        report = (
            similarity_service.report_for_signal(message.from_user.id, int(parts[1]))
            if len(parts) > 1 else similarity_service.latest_report(message.from_user.id)
        )
    except (ValueError, LookupError) as exc:
        await message.answer(
            f"🧬 <b>Similar Trade Intelligence</b>\n\n{public_error_message(exc, context='RESEARCH')}\n"
            "Usage: <code>/copy_similar</code> or <code>/copy_similar 123</code>",
            parse_mode="HTML",
        )
        return

    if report["found"] == 0:
        await message.answer(
            f"🧬 <b>Similar Trade Intelligence · #{report['signal_id']}</b>\n\n"
            "No sufficiently similar resolved execution or shadow trade exists yet. "
            "The Strategy Genome has been created and the report will improve as history grows.",
            parse_mode="HTML",
        )
        return

    breakdown = "\n".join(
        f"• {escape(group)}: <b>{score:.0f}%</b>"
        for group, score in report["breakdown"].items()
    ) or "• Not enough shared features"
    matches = "\n\n".join(
        f"• Replay <code>#{item.signal_id}</code> · {escape(item.symbol)} {escape(item.side)} {escape(item.timeframe.upper())}\n"
        f"  Similarity: <b>{item.similarity:.0f}%</b> · {item.realized_r:+.2f}R · {escape(item.source)}\n"
        f"  Matched: {_render_feature_list(list(item.matched_features))}\n"
        f"  Different: {_render_feature_list(list(item.different_features))}"
        for item in report["matches"]
    )
    confidence = report["statistical_confidence"]
    text = f"""🧬 <b>Similar Trade Intelligence · #{report['signal_id']}</b>

Found: <b>{report['found']} similar resolved trades</b>
Displayed: <b>{report['shown']} closest replays</b>
Average similarity: <b>{report['average_similarity']:.1f}%</b>
Statistical confidence: <b>{confidence['level']}</b> ({confidence['score']:.0f}/100)
Win rate: <b>{report['win_rate']:.1f}%</b>
Average R: <b>{report['average_r']:+.2f}R</b>
Average MFE: <b>{report['average_mfe']:+.2f}%</b>
Average MAE: <b>{report['average_mae']:+.2f}%</b>
Genome: <code>{report['fingerprint']}</code>

📊 <b>Similarity Breakdown</b>
{breakdown}

✅ <b>Top matching features</b>
{_render_feature_list(report['top_matching_features'])}

⚠️ <b>Largest differences</b>
{_render_feature_list(report['largest_differences'])}

🎞 <b>Closest Replays</b>
{matches}

Executed and zero-exposure shadow outcomes are included. Open trades are excluded to prevent outcome leakage."""
    await message.answer(text, parse_mode="HTML")


@router.message(Command("genome"))
async def genome(message: Message):
    parts = (message.text or "").split()
    try:
        report = (
            similarity_service.genome_for_signal(int(parts[1]))
            if len(parts) > 1 else similarity_service.latest_genome(message.from_user.id)
        )
    except (ValueError, LookupError) as exc:
        await message.answer(
            f"🧬 <b>Strategy Genome</b>\n\n{public_error_message(exc, context='RESEARCH')}\n"
            "Usage: <code>/genome</code> or <code>/genome 123</code>",
            parse_mode="HTML",
        )
        return

    sections: list[str] = []
    for group, values in report["groups"].items():
        rows = "\n".join(
            f"• {escape(key.replace('_', ' ').title())}: <b>{_render_genome_value(value)}</b>"
            for key, value in values.items()
        )
        sections.append(f"<b>{escape(group)}</b>\n{rows}")
    text = (
        f"🧬 <b>Strategy Genome · #{report['signal_id']}</b>\n\n"
        f"Fingerprint: <code>{report['fingerprint']}</code>\n\n"
        + "\n\n".join(sections)
        + "\n\nThis is the normalized execution-time context used by Similarity Intelligence."
    )
    await message.answer(text, parse_mode="HTML")

@router.message(Command("copy_queue"))
async def copy_queue(message: Message):
    counts = service.execution_queue.summary(message.from_user.id)
    recent = service.execution_queue.recent(message.from_user.id, limit=8)
    rows = "\n".join(
        f"• <code>{escape(str(row['status']))}</code> · {escape(str(row.get('symbol') or json_symbol(row)))} · "
        f"<code>{escape(str(row['idempotency_key']))}</code>"
        for row in recent
    ) or "• Queue is empty"
    await message.answer(
        "⚙️ <b>Copy Execution Queue</b>\n\n"
        f"Planned: <b>{counts.get('PLANNED', 0)}</b>\n"
        f"Executing: <b>{counts.get('EXECUTING', 0)}</b>\n"
        f"Executed: <b>{counts.get('EXECUTED', 0)}</b>\n"
        f"Rejected: <b>{counts.get('REJECTED', 0)}</b>\n"
        f"Failed: <b>{counts.get('FAILED', 0)}</b>\n"
        f"Total: <b>{counts.get('TOTAL', 0)}</b>\n\n"
        f"<b>Recent</b>\n{rows}",
        parse_mode="HTML",
    )


def json_symbol(row: dict) -> str:
    try:
        import json
        return str(json.loads(str(row.get("plan_json") or "{}" )).get("symbol") or "—")
    except (TypeError, ValueError, json.JSONDecodeError):
        return "—"


@router.message(Command("copy_plan"))
async def copy_plan(message: Message):
    rows = service.execution_queue.recent(message.from_user.id, limit=1)
    if not rows:
        await message.answer("📭 No copy execution plans yet.")
        return
    row = rows[0]
    try:
        import json
        payload = json.loads(str(row.get("plan_json") or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = {}
    await message.answer(
        "🧾 <b>Latest Copy Plan</b>\n\n"
        f"Symbol: <b>{escape(str(payload.get('symbol') or '—'))}</b>\n"
        f"Side: <b>{escape(str(payload.get('side') or '—'))}</b>\n"
        f"Status: <b>{escape(str(row.get('status') or '—'))}</b>\n"
        f"Code: <code>{escape(str(row.get('code') or '—'))}</code>\n"
        f"Reason: {public_error_message(row.get('last_error'), context='COPY') if row.get('last_error') else escape(str(row.get('reason') or '—'))}\n"
        f"Quantity: <b>{escape(str(payload.get('quantity') or '—'))}</b>\n"
        f"Notional: <b>{escape(str(payload.get('notional') or '—'))}</b>\n"
        f"Leverage: <b>{escape(str(payload.get('leverage') or 1))}x</b>\n"
        f"Key: <code>{escape(str(row.get('idempotency_key') or '—'))}</code>",
        parse_mode="HTML",
    )

# v9.9.5g — Unified Paper Execution Lifecycle
from services.execution_inspection import ExecutionInspectionService
from services.paper_execution_lifecycle import PaperExecutionLifecycle

execution_inspection_service = ExecutionInspectionService()
paper_lifecycle_service = PaperExecutionLifecycle()


def _compact_ref(value: object, length: int = 18) -> str:
    raw = str(value or "—")
    return raw if len(raw) <= length else raw[: length - 1] + "…"


def _execution_status_icon(status: str) -> str:
    return {
        "PLANNED": "📝", "REJECTED": "⛔", "EXECUTING": "⚙️", "EXECUTED": "✅",
        "SUBMITTED": "📨", "ACCEPTED": "🤝", "OPEN": "🟢", "PARTIALLY_FILLED": "🟡",
        "FILLED": "✅", "FAILED": "❌", "CANCELLED": "🚫", "EXPIRED": "⌛",
    }.get(status, "•")


def _render_order(row: dict) -> str:
    status = str(row.get("status") or "UNKNOWN")
    requested = float(row.get("requested_quantity") or 0.0)
    filled = float(row.get("filled_quantity") or 0.0)
    average = row.get("average_fill_price")
    return (
        f"{_execution_status_icon(status)} <b>Order #{int(row['id'])} · {escape(str(row.get('symbol') or 'UNKNOWN'))} "
        f"{escape(str(row.get('side') or '—'))}</b>\n"
        f"Status: <code>{escape(status)}</code> · Filled: <b>{filled:g}/{requested:g}</b>\n"
        f"Average: <b>{'—' if average is None else f'{float(average):g}'}</b> · Type: <b>{escape(str(row.get('order_type') or '—'))}</b>\n"
        f"Signal: <code>#{int(row.get('signal_id') or 0)}</code> · Ref: <code>{escape(_compact_ref(row.get('execution_ref') or row.get('idempotency_key')))}</code>"
    )


def _render_legacy_execution_item(item) -> str:
    row, plan = item.journal, item.plan
    status = str(row.get("status") or "UNKNOWN")
    quantity = plan.get("quantity")
    notional = plan.get("notional")
    qty_text = "—" if quantity is None else f"{float(quantity):.8f}".rstrip("0").rstrip(".")
    notional_text = "—" if notional is None else f"${float(notional):,.2f}"
    return (
        f"{_execution_status_icon(status)} <b>Execution #{int(row['id'])} · {escape(str(plan.get('symbol') or 'UNKNOWN'))} "
        f"{escape(str(plan.get('side') or '—'))}</b>\n"
        f"Status: <code>{escape(status)}</code> · Qty: <b>{qty_text}</b> · Notional: <b>{notional_text}</b>\n"
        f"Signal: <code>#{int(row.get('signal_id') or 0)}</code> · Ref: <code>{escape(_compact_ref(row.get('execution_ref') or row.get('idempotency_key')))}</code>"
    )


def _render_execution_detail(item) -> str:
    row, plan = item.journal, item.plan
    status = str(row.get("status") or "UNKNOWN")
    timeline = list(reversed(item.timeline))
    timeline_text = "\n".join(
        f"• <code>{escape(str(event.get('to_status') or 'UNKNOWN'))}</code> · "
        f"{escape(str(event.get('actor') or 'system'))} · {escape(str(event.get('reason_code') or '—'))}"
        for event in timeline[-12:]
    ) or "• Historical transitions were not recorded for this older execution."
    tps = plan.get("take_profits") or []
    tp_text = " / ".join(f"{float(value):g}" for value in tps) if tps else "—"
    return f"""⚙️ <b>Execution #{int(row['id'])}</b>

Symbol: <b>{escape(str(plan.get('symbol') or 'UNKNOWN'))}</b>
Side: <b>{escape(str(plan.get('side') or '—'))}</b>
Timeframe: <b>{escape(str(plan.get('timeframe') or '—'))}</b>
Status: {_execution_status_icon(status)} <code>{escape(status)}</code>
Order type: <b>{escape(str(plan.get('order_type') or '—'))}</b>
Quantity: <b>{'—' if plan.get('quantity') is None else f"{float(plan['quantity']):g}"}</b>
Notional: <b>{'—' if plan.get('notional') is None else f"${float(plan['notional']):,.2f}"}</b>
Entry: <b>{'—' if plan.get('entry_price') is None else f"{float(plan['entry_price']):g}"}</b>
Stop: <b>{'—' if plan.get('stop_loss') is None else f"{float(plan['stop_loss']):g}"}</b>
Targets: <b>{tp_text}</b>
Leverage: <b>{int(plan.get('leverage') or 1)}x</b>
Attempts: <b>{int(row.get('attempt_count') or 0)}</b>
Code: <code>{escape(str(row.get('code') or '—'))}</code>
Reason: {public_error_message(row.get('last_error'), context='COPY') if row.get('last_error') else escape(str(row.get('reason') or '—'))}
Execution ref: <code>{escape(str(row.get('execution_ref') or '—'))}</code>
Idempotency: <code>{escape(str(row.get('idempotency_key') or '—'))}</code>

🧭 <b>Execution timeline</b>
{timeline_text}"""


def _render_order_detail(order: dict, events: list[dict], fills: list[dict], position: dict | None) -> str:
    event_text = "\n".join(
        f"• <code>{escape(str(e.get('to_status') or 'UNKNOWN'))}</code> · {escape(str(e.get('actor') or 'system'))} · {escape(str(e.get('reason_code') or '—'))}"
        for e in events[-12:]
    ) or "• No order transitions recorded."
    fill_text = "\n".join(
        f"• {float(f.get('quantity') or 0):g} @ <b>{float(f.get('price') or 0):g}</b> · fee ${float(f.get('commission') or 0):.4f}"
        for f in fills[-8:]
    ) or "• No fills recorded."
    position_text = "No position created."
    if position:
        position_text = (
            f"{escape(str(position.get('status') or 'UNKNOWN'))} · Qty <b>{float(position.get('quantity') or 0):g}</b> · "
            f"Avg <b>{float(position.get('average_entry') or 0):g}</b> · Fees <b>${float(position.get('total_commission') or 0):.4f}</b>"
        )
    return f"""📦 <b>Paper Order #{int(order['id'])}</b>

Symbol: <b>{escape(str(order.get('symbol') or 'UNKNOWN'))}</b>
Side: <b>{escape(str(order.get('side') or '—'))}</b>
Timeframe: <b>{escape(str(order.get('timeframe') or '—'))}</b>
Status: {_execution_status_icon(str(order.get('status')))} <code>{escape(str(order.get('status') or 'UNKNOWN'))}</code>
Type: <b>{escape(str(order.get('order_type') or '—'))}</b>
Requested: <b>{float(order.get('requested_quantity') or 0):g}</b>
Filled: <b>{float(order.get('filled_quantity') or 0):g}</b>
Average fill: <b>{'—' if order.get('average_fill_price') is None else f"{float(order['average_fill_price']):g}"}</b>
Execution ref: <code>{escape(str(order.get('execution_ref') or '—'))}</code>

🧭 <b>Order timeline</b>
{event_text}

🧾 <b>Fills</b>
{fill_text}

📈 <b>Position</b>
{position_text}"""


@router.message(Command("orders"))
async def execution_orders(message: Message):
    orders = paper_lifecycle_service.recent_orders(message.from_user.id, limit=10)
    if orders:
        await message.answer(
            "📦 <b>Latest Paper Orders</b>\n\n" + "\n\n".join(_render_order(row) for row in orders)
            + "\n\nUse <code>/execution ORDER_ID</code> for the full lifecycle.", parse_mode="HTML",
        )
        return
    items = execution_inspection_service.recent(message.from_user.id, limit=10)
    if not items:
        await message.answer("📦 <b>Paper Orders</b>\n\nNo execution plans have been recorded yet.", parse_mode="HTML")
        return
    await message.answer(
        "⚙️ <b>Historical Execution Records</b>\n\n" + "\n\n".join(_render_legacy_execution_item(item) for item in items)
        + "\n\nNew accepted/rejected signals will also create unified paper-order records.", parse_mode="HTML",
    )


@router.message(Command("execution"))
async def execution_detail(message: Message):
    parts = (message.text or "").split(maxsplit=1)
    reference = parts[1].strip() if len(parts) > 1 else ""
    if reference:
        order = paper_lifecycle_service.get_order_for_user(message.from_user.id, reference)
        if order:
            events = paper_lifecycle_service.order_events(message.from_user.id, int(order["id"]))
            fills = [f for f in paper_lifecycle_service.recent_fills(message.from_user.id, limit=100) if int(f["order_id"]) == int(order["id"])]
            position = paper_lifecycle_service.position_for_order(int(order["id"]))
            await message.answer(_render_order_detail(order, events, fills, position), parse_mode="HTML")
            return
        item = execution_inspection_service.get(message.from_user.id, reference)
    else:
        orders = paper_lifecycle_service.recent_orders(message.from_user.id, limit=1)
        if orders:
            order = orders[0]
            events = paper_lifecycle_service.order_events(message.from_user.id, int(order["id"]))
            fills = [f for f in paper_lifecycle_service.recent_fills(message.from_user.id, limit=100) if int(f["order_id"]) == int(order["id"])]
            position = paper_lifecycle_service.position_for_order(int(order["id"]))
            await message.answer(_render_order_detail(order, events, fills, position), parse_mode="HTML")
            return
        items = execution_inspection_service.recent(message.from_user.id, limit=1)
        item = items[0] if items else None
    if item is None:
        await message.answer("Execution not found. Use an order ID, signal ID, plan ID, idempotency key, or execution reference.")
        return
    await message.answer(_render_execution_detail(item), parse_mode="HTML")


@router.message(Command("fills"))
async def execution_fills(message: Message):
    fills = paper_lifecycle_service.recent_fills(message.from_user.id, limit=15)
    if not fills:
        await message.answer(
            "🧾 <b>Paper Fills</b>\n\nNo unified fills have been recorded yet. "
            "They will appear after the next approved paper execution.", parse_mode="HTML",
        )
        return
    lines = [
        f"✅ <b>{escape(str(f.get('symbol') or 'UNKNOWN'))} {escape(str(f.get('side') or '—'))}</b> · "
        f"{float(f.get('quantity') or 0):g} @ <b>{float(f.get('price') or 0):g}</b>\n"
        f"Notional: <b>${float(f.get('notional') or 0):,.2f}</b> · Fee: <b>${float(f.get('commission') or 0):.4f}</b> · "
        f"Slippage: <b>{float(f.get('slippage_pct') or 0):.3f}%</b>"
        for f in fills
    ]
    await message.answer("🧾 <b>Latest Paper Fills</b>\n\n" + "\n\n".join(lines), parse_mode="HTML")


@router.message(Command("positions"))
async def execution_positions(message: Message):
    positions = paper_lifecycle_service.recent_positions(message.from_user.id, limit=15)
    if not positions:
        report = service.reconciliation.reconcile(message.from_user.id)
        await message.answer(
            "📈 <b>Unified Paper Positions</b>\n\nNo positions have been created by the new lifecycle yet.\n\n"
            f"Legacy open: <b>{report.legacy_open_count}</b>\n"
            f"Confirmed legacy active: <b>{report.confirmed_active_legacy_count}</b>\n"
            f"Unified open: <b>{report.unified_open_count}</b>\n"
            f"Unresolved legacy: <b>{report.unresolved_legacy_count}</b> ({report.unresolved_heat_r:.2f}R)\n"
            f"Confirmed heat: <b>{report.confirmed_active_heat_r:.2f}R</b>\n"
            f"Heat source: <b>{report.heat_source}</b>\n"
            f"Reconciliation: <b>{report.status}</b>\n"
            f"Mismatch: <b>{'YES' if report.mismatch_detected else 'NO'}</b>\n"
            f"Stale legacy rows closed: <b>{report.stale_legacy_closed_count}</b>",
            parse_mode="HTML",
        )
        return
    lines = [
        f"{'🟢' if str(p.get('status')) == 'OPEN' else '⚪'} <b>#{int(p['id'])} · {escape(str(p.get('symbol') or 'UNKNOWN'))} {escape(str(p.get('side') or '—'))}</b>\n"
        f"Status: <code>{escape(str(p.get('status') or 'UNKNOWN'))}</code> · Qty: <b>{float(p.get('quantity') or 0):g}</b> · "
        f"Avg: <b>{float(p.get('average_entry') or 0):g}</b>\n"
        f"Realized: <b>${float(p.get('realized_pnl') or 0):+.2f}</b> · Unrealized: <b>${float(p.get('unrealized_pnl') or 0):+.2f}</b> · "
        f"Fees: <b>${float(p.get('total_commission') or 0):.4f}</b>"
        for p in positions
    ]
    await message.answer("📈 <b>Unified Paper Positions</b>\n\n" + "\n\n".join(lines), parse_mode="HTML")
