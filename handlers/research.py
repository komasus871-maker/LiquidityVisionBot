from __future__ import annotations

from html import escape

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from services.capabilities import CapabilityService
from services.edge_discovery import EdgeDiscoveryEngine
from services.research_engine import ResearchEngine


router = Router()
engine = ResearchEngine()
edge_engine = EdgeDiscoveryEngine()
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
        "<b>Research Engine</b>\n\n"
        f"Immutable snapshots / resolved: <b>{report['snapshots']} / {report['resolved']}</b>\n"
        f"{_metric_lines(report['overall'])}\n\n"
        f"Late backfills excluded from metrics: <b>{int(report['overall'].get('late_backfill_excluded') or 0)}</b>\n\n"
        "Manual/panic outcomes and non-decision-time backfills are excluded from pure strategy metrics. "
        "Results are descriptive and do not establish causality or future profitability.\n\n"
        "<code>/strategy_lab</code> · <code>/regimes</code> · <code>/edge_report</code>\n"
        "<code>/signal_rankings</code> · <code>/scalping_research</code>\n"
        "<code>/edge_discovery</code> · <code>/hypotheses</code> · <code>/forward_tests</code>",
        parse_mode="HTML",
    )


@router.message(Command("strategy_lab", "strategy_compare"))
async def strategy_lab(message: Message):
    report = engine.strategy_comparison(message.from_user.id)
    lines = []
    for item in report["strategies"]:
        lines.append(
            f"<b>{escape(item['strategy'])}</b> · accepted {item['accepted']}/{item['identical_resolved_snapshots']} "
            f"· WR {_number(None if item['win_rate'] is None else item['win_rate'] * 100, '%', 1)} "
            f"· E {_number(item['expectancy_r'], 'R')} · <code>{item['status']}</code>"
        )
    await message.answer(
        "<b>Strategy Lab</b>\n\n" + ("\n".join(lines) or "No resolved identical snapshots yet.") +
        "\n\nAll strategies are versioned SHADOW decisions over the same snapshots. "
        "They cannot open or modify positions.", parse_mode="HTML",
    )


@router.message(Command("regimes"))
async def regimes(message: Message):
    report = edge_engine.cohort_edges(message.from_user.id)
    items = report["dimensions"]["regime"]
    lines = [
        f"<b>{escape(item['cohort'])}</b> · n={item['sample_size']} · "
        f"WR {_number(None if item['win_rate'] is None else item['win_rate'] * 100, '%', 1)} · "
        f"E {_number(item['expectancy_r'], 'R')} · <code>{item['evidence_state']}</code>"
        for item in items[:12]
    ]
    await message.answer(
        "<b>Regime Research</b>\n\n" + ("\n".join(lines) or "No regime snapshots yet.") +
        "\n\nOverlapping regime tags are evaluated independently from immutable decision-time snapshots.",
        parse_mode="HTML",
    )


@router.message(Command("edge_report"))
async def edge_report(message: Message):
    report = engine.edge_report(message.from_user.id)
    strongest = report["strongest_descriptive_cohorts"][:6]
    lines = [
        f"<b>{escape(item['dimension'])}: {escape(item['cohort'])}</b> · n={item['sample_size']} "
        f"· E {_number(item['expectancy_r'], 'R')} · PF {_number(item['profit_factor'])}"
        for item in strongest
    ]
    await message.answer(
        "<b>Descriptive Edge Report</b>\n\n" +
        ("\n".join(lines) if lines else "No cohort meets the configured minimum sample size.") +
        "\n\nNo causal or profitability claim is made. Manual outcomes are excluded.",
        parse_mode="HTML",
    )


@router.message(Command("signal_rankings"))
async def signal_rankings(message: Message):
    rows = engine.rankings(message.from_user.id, limit=10)
    lines = [
        f"<b>#{index} {escape(str(row['symbol']))} {escape(str(row['timeframe']))} {escape(str(row['side']))}</b> "
        f"· {float(row['diagnostic_score']):.1f}/100 · <code>{escape(str(row['primary_regime']))}</code>"
        for index, row in enumerate(rows, 1)
    ]
    await message.answer(
        "<b>Research Signal Ranking</b>\n\n" + ("\n".join(lines) or "No ranked snapshots yet.") +
        "\n\nDiagnostic only; ranking has no execution authority.", parse_mode="HTML",
    )


@router.message(Command("scalping_research"))
async def scalping_research(message: Message):
    report = edge_engine.scalping_lab(message.from_user.id)
    lines = [
        f"<b>{escape(item['timeframe'])} {escape(item['strategy_family'])}</b> · "
        f"n={item['after_cost_metrics']['sample_size']} · after-cost E "
        f"{_number(item['after_cost_metrics']['expectancy_r'], 'R')} · <code>{item['evidence_state']}</code>"
        for item in report["candidates"]
    ]
    await message.answer(
        "<b>Scalping Research (PAPER/SHADOW)</b>\n\n" +
        ("\n".join(lines) or "No resolved 1m/3m/5m samples yet.") +
        f"\n\nAssumed round-trip cost: <b>{report['roundtrip_cost_pct']:.3f}%</b>. "
        "Evidence requires sufficient samples and gross movement materially above modeled costs.",
        parse_mode="HTML",
    )


@router.message(Command("capabilities"))
async def capability_status(message: Message):
    snapshot = capabilities.snapshot(message.from_user.id)
    lines = [
        f"<b>{escape(name.replace('_', ' ').title())}</b>: {'available' if item['enabled'] else 'unavailable'} "
        f"· <code>{escape(str(item['mode']))}</code>"
        for name, item in snapshot.items()
    ]
    await message.answer(
        "<b>Product Capabilities</b>\n\n" + "\n".join(lines) +
        "\n\nCapability metadata is centralized and does not bypass risk or execution policy.",
        parse_mode="HTML",
    )


@router.message(Command("edge_discovery"))
async def edge_discovery(message: Message):
    report = edge_engine.edge_dashboard(message.from_user.id)
    overall = report["overall"]
    states = ", ".join(f"{key}: {value}" for key, value in sorted(report["hypothesis_states"].items())) or "none"
    await message.answer(
        "<b>Edge Discovery</b>\n\n"
        f"Eligible resolved samples: <b>{overall['sample_size']}</b> "
        f"(<code>{overall['sample_tier']}</code>)\n"
        f"Evidence: <code>{overall['evidence_state']}</code>\n"
        f"Expectancy: <b>{_number(overall['expectancy_r'], 'R')}</b>\n"
        f"Persisted findings: <b>{report['findings']}</b>\n"
        f"Hypotheses: {escape(states)}\n\n"
        "Only trustworthy decision-time snapshots are eligible. Findings are descriptive and cannot change execution.",
        parse_mode="HTML",
    )


@router.message(Command("feature_edge"))
async def feature_edge(message: Message):
    report = edge_engine.feature_contributions(message.from_user.id)
    items = [item for item in report["features"] if item["expectancy_delta_r"] is not None]
    items.sort(key=lambda item: (-abs(item["expectancy_delta_r"]), item["feature"]))
    lines = [
        f"<b>{escape(item['feature'])}</b> · n={item['present']['sample_size']}/{item['absent']['sample_size']} "
        f"· ΔE {_number(item['expectancy_delta_r'], 'R')} · <code>{item['evidence_state']}</code>"
        for item in items[:10]
    ]
    await message.answer(
        "<b>Feature Contribution Research</b>\n\n" +
        ("\n".join(lines) or "No feature comparison has enough resolved data yet.") +
        "\n\nComparisons are stratified by timeframe, regime, and direction when samples permit. No causal claim.",
        parse_mode="HTML",
    )


@router.message(Command("hypotheses"))
async def hypotheses(message: Message):
    rows = edge_engine.hypotheses(message.from_user.id, limit=10)
    lines = [
        f"<b>{escape(row['hypothesis_text'][:110])}</b>\n"
        f"<code>{row['lifecycle_state']}</code> · <code>{row['evidence_state']}</code> "
        f"· forward n={int((row['forward_metrics'] or {}).get('sample_size') or 0)}"
        for row in rows
    ]
    await message.answer(
        "<b>Frozen Research Hypotheses</b>\n\n" +
        ("\n\n".join(lines) or "No hypothesis has passed the discovery sample gate.") +
        "\n\nDefinitions are immutable after discovery and cannot promote themselves to production.",
        parse_mode="HTML",
    )


@router.message(Command("forward_tests"))
async def forward_tests(message: Message):
    rows = edge_engine.hypotheses(message.from_user.id, limit=20)
    rows = [row for row in rows if row["lifecycle_state"] in {"FORWARD_TESTING", "CONFIRMED", "REJECTED"}]
    lines = [
        f"<b>{escape(row['hypothesis_text'][:90])}</b> · <code>{row['lifecycle_state']}</code> "
        f"· n={int((row['forward_metrics'] or {}).get('sample_size') or 0)}/{row['minimum_forward_samples']}"
        for row in rows[:10]
    ]
    await message.answer(
        "<b>Forward Validation</b>\n\n" + ("\n".join(lines) or "No frozen forward cohorts yet.") +
        "\n\nOnly signals strictly after each frozen discovery cutoff are counted.", parse_mode="HTML")


@router.message(Command("rr_research"))
async def rr_research(message: Message):
    report = edge_engine.rr_research(message.from_user.id)
    lines = [
        f"<b>{item['target_r']:g}R</b> · certain n={item['certain_path_samples']} "
        f"· ambiguous={item['intrabar_order_uncertain']} · E {_number(item['metrics']['expectancy_r'], 'R')}"
        for item in report["policies"]
    ]
    await message.answer(
        "<b>RR Research</b>\n\n" + "\n".join(lines) +
        "\n\nSamples with ambiguous target/stop ordering are excluded; OHLC ordering is never invented.",
        parse_mode="HTML")


@router.message(Command("exit_research"))
async def exit_research(message: Message):
    report = edge_engine.exit_research(message.from_user.id)
    lines = [f"<b>{escape(item['policy'])}</b> · <code>{item['status']}</code>"
             for item in report["policies"]]
    await message.answer(
        "<b>Exit Policy Research</b>\n\n" + "\n".join(lines) +
        "\n\nUnavailable ordered-path evidence is reported as unavailable, not estimated.", parse_mode="HTML")


@router.message(Command("confidence_research"))
async def confidence_research(message: Message):
    report = edge_engine.confidence_calibration(message.from_user.id)
    lines = [
        f"<b>{item['bucket']}</b> · n={item['sample_size']} · WR "
        f"{_number(None if item['observed_win_rate'] is None else item['observed_win_rate'] * 100, '%', 1)} "
        f"· gap {_number(None if item['calibration_gap'] is None else item['calibration_gap'] * 100, '%', 1)}"
        for item in report["buckets"]
    ]
    await message.answer(
        "<b>Confidence Calibration</b>\n\n" + ("\n".join(lines) or "No resolved confidence buckets yet.") +
        "\n\nHistorical deterministic scores are never rewritten.", parse_mode="HTML")


@router.message(Command("portfolio_edge"))
async def portfolio_edge(message: Message):
    report = edge_engine.portfolio_edge(message.from_user.id)
    await message.answer(
        "<b>Portfolio Edge Research</b>\n\n"
        f"Simultaneous 15-minute windows: <b>{report['simultaneous_windows']}</b>\n"
        f"Clustered expectancy: <b>{_number(report['clustered']['expectancy_r'], 'R')}</b>\n"
        f"Same-direction expectancy: <b>{_number(report['same_direction']['expectancy_r'], 'R')}</b>\n"
        f"Repeated-symbol expectancy: <b>{_number(report['same_symbol']['expectancy_r'], 'R')}</b>\n\n"
        "No production portfolio optimization is applied.", parse_mode="HTML")


@router.message(Command("ai_research_compare"))
async def ai_research_compare(message: Message):
    report = edge_engine.ai_comparison(message.from_user.id)
    await message.answer(
        "<b>Deterministic vs GPT vs Research</b>\n\n"
        f"Resolved matched samples: <b>{report['sample_size']}</b>\n"
        f"Deterministic expectancy: <b>{_number(report.get('deterministic_expectancy_r'), 'R')}</b>\n"
        f"GPT expectancy: <b>{_number(report.get('gpt_expectancy_r'), 'R')}</b>\n"
        f"Research walk-forward expectancy: <b>{_number(report.get('research_expectancy_r'), 'R')}</b>\n"
        f"Research-scored samples: <b>{int(report.get('research_scored_samples') or 0)}</b>\n"
        f"Status: <code>{escape(str(report.get('status') or 'INSUFFICIENT'))}</code>\n\n"
        "All three remain independent and advisory; no future outcome is placed in GPT context.",
        parse_mode="HTML")
