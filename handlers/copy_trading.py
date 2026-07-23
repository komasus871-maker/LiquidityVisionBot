from __future__ import annotations

from html import escape

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from services.copy_trading import CopyTradingService
from services.copy_training import CopyTrainingService
from services.copy_execution_intelligence import CopyExecutionIntelligenceService
from services.copy_guardrail_outcomes import CopyGuardrailOutcomeService
from services.copy_similarity import CopySimilarityService

router = Router()
service = CopyTradingService()
training_service = CopyTrainingService()
intelligence_service = CopyExecutionIntelligenceService()
outcome_service = CopyGuardrailOutcomeService()
similarity_service = CopySimilarityService()


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
Top rejection: <b>{stats.get("top_rejection_code") or "—"}</b> ({int(stats.get("top_rejection_count") or 0)})
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
<code>/copy_training</code> — adaptive learning report
<code>/copy_rejections</code> — execution rejection intelligence
<code>/copy_guardrails</code> — rejected-signal outcome report
<code>/copy_similar [signal_id]</code> — explainable similar-trade intelligence
<code>/genome [signal_id]</code> — inspect Strategy Genome
<code>/panic</code> — close paper positions and disable execution

🧬 v9.8.1 keeps explainable similarity and adds a read-only Binance exchange foundation.
🔌 Use /exchanges to inspect connectivity; LIVE order submission is not available.
🔒 LIVE execution remains fail-closed."""


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
            f"🧬 <b>Similar Trade Intelligence</b>\n\n{escape(str(exc))}\n"
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
            f"🧬 <b>Strategy Genome</b>\n\n{escape(str(exc))}\n"
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
