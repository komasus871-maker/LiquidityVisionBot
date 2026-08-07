from __future__ import annotations

from html import escape

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from services.capabilities import CapabilityService
from services.research_engine import ResearchEngine


router = Router()
engine = ResearchEngine()
capabilities = CapabilityService()


def _number(value, suffix: str = "", digits: int = 2) -> str:
    return "n/a" if value is None else f"{float(value):.{digits}f}{suffix}"


def _metric_lines(metrics: dict) -> str:
    return (
        f"Samples: <b>{int(metrics.get('sample_size') or 0)}</b> "
        f"(<code>{escape(str(metrics.get('status') or 'UNKNOWN'))}</code>)\n"
        f"Win rate / expectancy: <b>{_number(None if metrics.get('win_rate') is None else metrics['win_rate'] * 100, '%', 1)}</b> "
        f"/ <b>{_number(metrics.get('expectancy_r'), 'R')}</b>\n"
        f"Average win / loss: <b>{_number(metrics.get('average_win_r'), 'R')} / {_number(metrics.get('average_loss_r'), 'R')}</b>\n"
        f"Profit factor: <b>{_number(metrics.get('profit_factor'))}</b>\n"
        f"MFE / MAE: <b>{_number(metrics.get('average_mfe_pct'), '%')} / {_number(metrics.get('average_mae_pct'), '%')}</b>\n"
        f"Drawdown proxy: <b>{_number(metrics.get('drawdown_proxy_r'), 'R')}</b>"
    )


@router.message(Command("research"))
async def research_dashboard(message: Message):
    report = engine.cohort_report(message.from_user.id)
    await message.answer(
        "ðŸ§ª <b>Research Engine</b>\n\n"
        f"Immutable snapshots / resolved: <b>{report['snapshots']} / {report['resolved']}</b>\n"
        f"{_metric_lines(report['overall'])}\n\n"
        f"Late backfills excluded from metrics: <b>{int(report['overall'].get('late_backfill_excluded') or 0)}</b>\n\n"
        "Manual/panic outcomes and non-decision-time backfills are excluded from pure strategy metrics. "
        "Results are descriptive and do not establish causality or future profitability.\n\n"
        "<code>/strategy_lab</code> Â· <code>/regimes</code> Â· <code>/edge_report</code>\n"
        "<code>/signal_rankings</code> Â· <code>/scalping_research</code>",
        parse_mode="HTML",
    )


@router.message(Command("strategy_lab", "strategy_compare"))
async def strategy_lab(message: Message):
    report = engine.strategy_comparison(message.from_user.id)
    lines = []
    for item in report["strategies"]:
        lines.append(
            f"<b>{escape(item['strategy'])}</b> Â· accepted {item['accepted']}/{item['identical_resolved_snapshots']} "
            f"Â· WR {_number(None if item['win_rate'] is None else item['win_rate'] * 100, '%', 1)} "
            f"Â· E {_number(item['expectancy_r'], 'R')} Â· <code>{item['status']}</code>"
        )
    await message.answer(
        "ðŸ§« <b>Strategy Lab</b>\n\n" + ("\n".join(lines) or "No resolved identical snapshots yet.") +
        "\n\nAll strategies are versioned SHADOW decisions over the same snapshots. "
        "They cannot open or modify positions.", parse_mode="HTML",
    )


@router.message(Command("regimes"))
async def regimes(message: Message):
    report = engine.cohort_report(message.from_user.id)
    items = report["dimensions"]["regime"]
    lines = [
        f"<b>{escape(item['cohort'])}</b> Â· n={item['sample_size']} Â· "
        f"WR {_number(None if item['win_rate'] is None else item['win_rate'] * 100, '%', 1)} Â· "
        f"E {_number(item['expectancy_r'], 'R')} Â· <code>{item['status']}</code>"
        for item in items[:12]
    ]
    await message.answer(
        "ðŸŒ <b>Regime Research</b>\n\n" + ("\n".join(lines) or "No regime snapshots yet.") +
        "\n\nOverlapping tags are retained in each immutable snapshot; this view groups by its primary diagnostic tag.",
        parse_mode="HTML",
    )


@router.message(Command("edge_report"))
async def edge_report(message: Message):
    report = engine.edge_report(message.from_user.id)
    strongest = report["strongest_descriptive_cohorts"][:6]
    lines = [
        f"<b>{escape(item['dimension'])}: {escape(item['cohort'])}</b> Â· n={item['sample_size']} "
        f"Â· E {_number(item['expectancy_r'], 'R')} Â· PF {_number(item['profit_factor'])}"
        for item in strongest
    ]
    await message.answer(
        "ðŸ“Š <b>Descriptive Edge Report</b>\n\n" +
        ("\n".join(lines) if lines else "No cohort meets the configured minimum sample size.") +
        "\n\nNo causal or profitability claim is made. Manual outcomes are excluded.",
        parse_mode="HTML",
    )


@router.message(Command("signal_rankings"))
async def signal_rankings(message: Message):
    rows = engine.rankings(message.from_user.id, limit=10)
    lines = [
        f"<b>#{index} {escape(str(row['symbol']))} {escape(str(row['timeframe']))} {escape(str(row['side']))}</b> "
        f"Â· {float(row['diagnostic_score']):.1f}/100 Â· <code>{escape(str(row['primary_regime']))}</code>"
        for index, row in enumerate(rows, 1)
    ]
    await message.answer(
        "ðŸ† <b>Research Signal Ranking</b>\n\n" + ("\n".join(lines) or "No ranked snapshots yet.") +
        "\n\nDiagnostic only; ranking has no execution authority.", parse_mode="HTML",
    )


@router.message(Command("scalping_research"))
async def scalping_research(message: Message):
    report = engine.scalping_report(message.from_user.id)
    lines = [
        f"<b>{escape(timeframe)}</b> Â· n={item['samples']} Â· after-cost E "
        f"{_number(item['after_cost_expectancy_r'], 'R')} Â· <code>{item['status']}</code>"
        for timeframe, item in report["timeframes"].items()
    ]
    await message.answer(
        "â± <b>Scalping Research (PAPER/SHADOW)</b>\n\n" +
        ("\n".join(lines) or "No resolved 1m/3m/5m samples yet.") +
        f"\n\nAssumed round-trip cost: <b>{report['roundtrip_cost_pct']:.3f}%</b>. "
        "Positive status requires both positive after-cost expectancy and the configured minimum sample size.",
        parse_mode="HTML",
    )


@router.message(Command("capabilities"))
async def capability_status(message: Message):
    snapshot = capabilities.snapshot(message.from_user.id)
    lines = [
        f"<b>{escape(name.replace('_', ' ').title())}</b>: {'available' if item['enabled'] else 'unavailable'} "
        f"Â· <code>{escape(str(item['mode']))}</code>"
        for name, item in snapshot.items()
    ]
    await message.answer(
        "ðŸ§© <b>Product Capabilities</b>\n\n" + "\n".join(lines) +
        "\n\nCapability metadata is centralized and does not bypass risk or execution policy.",
        parse_mode="HTML",
    )
