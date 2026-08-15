from __future__ import annotations

from html import escape

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, Message

from services.capabilities import CapabilityService
from services.edge_discovery import EdgeDiscoveryEngine
from services.market_intelligence import concise_market_story
from services.market_intelligence_repository import MarketIntelligenceRepository
from services.research_engine import ResearchEngine
from services.usage_policy import UsagePolicyService
from services.user_analytics_export import UserAnalyticsExportService
from utils.symbols import normalize_usdt_symbol


router = Router()
engine = ResearchEngine()
edge_engine = EdgeDiscoveryEngine()
capabilities = CapabilityService()
market_repo = MarketIntelligenceRepository()
usage = UsagePolicyService()


async def _require_capability(message: Message, capability: str) -> bool:
    if capabilities.has(message.from_user.id, capability):
        return True
    await message.answer(
        f"<b>Preview</b>\n\n{escape(capabilities.preview(capability))}", parse_mode="HTML")
    return False


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


def _signal_argument(message: Message) -> int | None:
    parts = (message.text or "").split()
    return int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None


def _symbol_argument(message: Message) -> str | None:
    parts = (message.text or "").split()
    if len(parts) <= 1:
        return None
    try:
        return normalize_usdt_symbol(parts[1])
    except ValueError:
        return None


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


@router.message(Command("export_analytics"))
async def export_analytics(message: Message):
    if not await _require_capability(message, "EXPORT_RESEARCH_DATA"):
        return
    parts = (message.text or "").split()
    format_name = parts[1].lower() if len(parts) > 1 else "json"
    try:
        days = int(parts[2]) if len(parts) > 2 else 90
    except ValueError:
        await message.answer("Usage: <code>/export_analytics [json|csv] [1-365 days]</code>",
                             parse_mode="HTML")
        return
    if format_name not in {"json", "csv"}:
        await message.answer("Usage: <code>/export_analytics [json|csv] [1-365 days]</code>",
                             parse_mode="HTML")
        return
    allowance = usage.consume(message.from_user.id, "RESEARCH_EXPORT", "export_daily",
                              metadata={"format": format_name, "days": max(1, min(days, 365))})
    if not allowance["allowed"]:
        await message.answer(
            f"Daily export limit reached. Reset: <code>00:00 UTC</code> · "
            f"remaining <b>{allowance['remaining']}</b>.", parse_mode="HTML")
        return
    try:
        filename, payload = UserAnalyticsExportService().build(
            message.from_user.id, format_name=format_name, days=days)
    except ValueError as exc:
        await message.answer(f"⚠️ <code>{escape(str(exc))}</code>. Use JSON or CSV.", parse_mode="HTML")
        return
    await message.answer_document(
        BufferedInputFile(payload, filename=filename),
        caption="Your user-scoped PAPER analytics export. It contains no exchange credentials or hidden AI reasoning.")


@router.message(Command("strategy_lab", "strategy_compare"))
async def strategy_lab(message: Message):
    if not await _require_capability(message, "RESEARCH_STRATEGY_LAB"):
        return
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


@router.message(Command("strategy_distribution"))
async def strategy_distribution(message: Message):
    report = market_repo.strategy_distribution(message.from_user.id)
    separation = market_repo.strategy_separation_diagnostics(message.from_user.id)
    lines = [f"<b>{escape(name.replace('_', ' ').title())}</b>: {count} ({count / max(1, report['classified']):.0%})"
             for name, count in report["distribution"]]
    margin = report["average_top_margin"]
    pair_lines = [
        f"<code>{escape(item['left'])}/{escape(item['right'])}</code> · n={item['n']} · "
        f"identical {_number(None if item['identical_decision_rate'] is None else item['identical_decision_rate'] * 100, '%', 1)} · "
        f"Jaccard {_number(item['jaccard_overlap'], '', 2)} · corr {_number(item['score_correlation'], '', 2)}"
        for item in separation["pairs"][:8]
    ]
    await message.answer(
        "<b>Strategy Fusion · Assignment Distribution</b>\n\n" +
        ("\n".join(lines) or "No classified decision snapshots yet.") +
        f"\n\nSnapshots: <b>{report['classified']}</b> · average lead: "
        f"<b>{'n/a' if margin is None else f'{margin:.1f}'}</b>\n"
        "Assignments follow evidence scores; diversity is never forced. Diagnostic only.\n\n"
        "<b>Strategy Separation V1</b>\n" +
        ("\n".join(pair_lines) or "Insufficient paired decision snapshots.") +
        "\nOutcome correlation remains unavailable until distinct strategy outcomes are persisted.")


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
@router.message(Command("rankings"))
async def signal_rankings(message: Message):
    if not await _require_capability(message, "ADVANCED_RANKING"):
        return
    rows = engine.rankings(message.from_user.id, limit=10)
    lines = []
    for index, row in enumerate(rows, 1):
        components = row.get("components") or {}
        advantages = components.get("strongest_advantages") or []
        weaknesses = components.get("strongest_weaknesses") or []
        advantage = advantages[0][0] if advantages else "unavailable"
        weakness = weaknesses[0][0] if weaknesses else "unavailable"
        lines.append(
            f"<b>#{index} {escape(str(row['symbol']))} {escape(str(row['timeframe']))} {escape(str(row['side']))}</b> "
            f"· {float(row['diagnostic_score']):.1f}/100 · <code>{escape(str(row['primary_regime']))}</code>\n"
            f"  + {escape(str(advantage).replace('_', ' ').lower())} · "
            f"− {escape(str(weakness).replace('_', ' ').lower())}"
        )
    comparison = ""
    if len(rows) >= 2:
        first, second = rows[0], rows[1]
        margin = float(first["diagnostic_score"]) - float(second["diagnostic_score"])
        comparison = (
            f"\n\n#1 leads #2 by <b>{margin:.1f}</b> quality points; inspect "
            f"<code>/signal_quality {int(first['signal_id'])}</code> for the full decomposition."
        )
    await message.answer(
        "<b>Research Signal Ranking</b>\n\n" + ("\n".join(lines) or "No ranked snapshots yet.") +
        comparison + "\n\nDiagnostic only; ranking has no execution authority.", parse_mode="HTML",
    )


@router.message(Command("scalping_research"))
async def scalping_research(message: Message):
    if not await _require_capability(message, "SCALPING_RESEARCH"):
        return
    report = edge_engine.scalping_lab(message.from_user.id)
    collection = report["collection"]
    lines = [
        f"<b>{escape(item['timeframe'])} {escape(item['strategy_family'])}</b> · "
        f"n={item['after_cost_metrics']['sample_size']} · after-cost E "
        f"{_number(item['after_cost_metrics']['expectancy_r'], 'R')} · <code>{item['evidence_state']}</code>"
        for item in report["candidates"]
    ]
    await message.answer(
        "<b>Scalping Research (PAPER/SHADOW)</b>\n\n" +
        ("\n".join(lines) or "No resolved 1m/3m/5m samples yet.") +
        f"\n\nCollection: <code>{escape(collection['state'])}</code> · "
        f"captured/resolved <b>{collection['captured']}/{collection['resolved']}</b>\n"
        f"Assumed round-trip cost: <b>{report['roundtrip_cost_pct']:.3f}%</b>. "
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
    if not await _require_capability(message, "RESEARCH_EDGE_DISCOVERY"):
        return
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
    if not await _require_capability(message, "RESEARCH_EDGE_DISCOVERY"):
        return
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
    if not await _require_capability(message, "RESEARCH_FORWARD_TESTS"):
        return
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
    if not await _require_capability(message, "RESEARCH_FORWARD_TESTS"):
        return
    rows = edge_engine.hypotheses(message.from_user.id, limit=20)
    rows = [row for row in rows if row["lifecycle_state"] in {"FORWARD_TESTING", "CONFIRMED", "REJECTED"}]
    lines = [
        f"<b>{escape(row['hypothesis_text'][:90])}</b> · <code>{row['lifecycle_state']}</code> "
        f"· n={int((row['forward_metrics'] or {}).get('sample_size') or 0)}/{row['minimum_forward_samples']}"
        for row in rows[:10]
    ]
    diagnostics = edge_engine.forward_diagnostics(message.from_user.id)
    await message.answer(
        "<b>Forward Validation</b>\n\n" + ("\n".join(lines) or "No frozen forward cohorts yet.") +
        f"\n\nPipeline: <code>{escape(diagnostics['state'])}</code> · eligible resolved "
        f"<b>{diagnostics['eligible_resolved_samples']}</b> · frozen <b>{diagnostics['frozen_hypotheses']}</b>\n"
        "Only signals strictly after each frozen discovery cutoff are counted.", parse_mode="HTML")


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
    if not await _require_capability(message, "PORTFOLIO_EDGE"):
        return
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
    if not await _require_capability(message, "AI_ADVANCED_COMMENTARY"):
        return
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


@router.message(Command("market_story"))
async def market_story(message: Message):
    if not await _require_capability(message, "MARKET_STORY_FULL"):
        return
    signal_id = _signal_argument(message)
    if signal_id is None:
        await message.answer("Usage: <code>/market_story SIGNAL_ID</code>", parse_mode="HTML")
        return
    row = market_repo.get_signal(signal_id, message.from_user.id)
    if not row:
        await message.answer("No decision-time market story exists for that signal.")
        return
    snapshot = row["full_snapshot"]
    story = snapshot.get("market_story") or {}
    await message.answer(
        f"<b>Market Story · #{signal_id}</b>\n\n"
        f"State: <code>{escape(str(story.get('state') or 'UNKNOWN'))}</code>\n"
        f"Transition: <code>{escape(str(story.get('transition') or 'UNKNOWN'))}</code>\n"
        f"{escape(concise_market_story(snapshot))}\n\n"
        "Grounded in immutable decision-time facts; no future data or actor identity claim.",
        parse_mode="HTML")


@router.message(Command("signal_quality"))
async def signal_quality(message: Message):
    if not await _require_capability(message, "SIGNAL_QUALITY_FULL"):
        return
    signal_id = _signal_argument(message)
    if signal_id is None:
        await message.answer("Usage: <code>/signal_quality SIGNAL_ID</code>", parse_mode="HTML")
        return
    row = market_repo.get_signal(signal_id, message.from_user.id)
    if not row:
        await message.answer("No decision-time quality snapshot exists for that signal.")
        return
    quality = row["quality"]
    families = sorted(((name, score) for name, score in (quality.get("family_scores") or {}).items()
                       if score is not None), key=lambda item: -float(item[1]))
    lines = [f"<b>{escape(name.replace('_', ' ').title())}</b>: {float(score):.1f}"
             for name, score in families[:8]]
    await message.answer(
        f"<b>Signal Quality V4 · #{signal_id}</b>\n\n"
        f"Overall / market: <b>{float(quality.get('overall_quality') or 0):.1f} / "
        f"{float(quality.get('market_quality') or 0):.1f}</b>\n"
        f"Evidence families / diversity: <b>{int(quality.get('evidence_family_count') or 0)} / "
        f"{float(quality.get('evidence_diversity_score') or 0):.1f}</b>\n\n"
        + "\n".join(lines) +
        "\n\nResearch score, not a probability and not an execution filter.", parse_mode="HTML")


@router.message(Command("contradictions"))
async def contradictions(message: Message):
    signal_id = _signal_argument(message)
    if signal_id is None:
        await message.answer("Usage: <code>/contradictions SIGNAL_ID</code>", parse_mode="HTML")
        return
    row = market_repo.get_signal(signal_id, message.from_user.id)
    if not row:
        await message.answer("No decision-time contradiction snapshot exists for that signal.")
        return
    quality = row["quality"]
    against = quality.get("contradicting_evidence") or []
    supports = quality.get("supporting_evidence") or []
    critical = quality.get("critical_disqualifiers") or []
    uncertainties = quality.get("uncertainties") or []
    def render(items):
        return "\n".join(f"• <code>{escape(str(item.get('severity') or 'INFO'))}</code> "
                          f"{escape(str(item.get('reason') or ''))}" for item in items) or "• none"
    await message.answer(
        f"<b>Contradictions · #{signal_id}</b>\n\n<b>Supporting</b>\n{render(supports)}\n\n"
        f"<b>Against</b>\n{render(against)}\n\n"
        f"Critical: <code>{escape(', '.join(map(str, critical)) or 'none')}</code>\n"
        f"Uncertainties: <code>{escape(', '.join(map(str, uncertainties)) or 'none')}</code>",
        parse_mode="HTML")


@router.message(Command("liquidity_map"))
async def liquidity_map(message: Message):
    symbol = _symbol_argument(message)
    if not symbol:
        await message.answer("Usage: <code>/liquidity_map BTCUSDT</code>", parse_mode="HTML")
        return
    row = market_repo.latest_symbol(symbol, message.from_user.id)
    if not row:
        await message.answer("No decision-time liquidity map exists for that symbol.")
        return
    liquidity = row["liquidity_map"]
    def side_lines(items):
        return "\n".join(f"• <code>{float(item.get('price') or 0):.8g}</code> · "
                          f"attraction {float(item.get('attraction_score') or 0):.0f} · "
                          f"{escape(str(item.get('state') or 'UNKNOWN'))}" for item in items[:5]) or "• none"
    await message.answer(
        f"<b>Liquidity Map · {escape(symbol)}</b>\n\n<b>Above</b>\n{side_lines(liquidity.get('above') or [])}\n\n"
        f"<b>Below</b>\n{side_lines(liquidity.get('below') or [])}\n\n"
        f"Unresolved / consumed: <b>{int(liquidity.get('unresolved_count') or 0)} / "
        f"{int(liquidity.get('consumed_count') or 0)}</b>", parse_mode="HTML")


@router.message(Command("orderbook"))
async def orderbook(message: Message):
    if not await _require_capability(message, "MICROSTRUCTURE_VIEW"):
        return
    symbol = _symbol_argument(message)
    if not symbol:
        await message.answer("Usage: <code>/orderbook BTCUSDT</code>", parse_mode="HTML")
        return
    row = market_repo.latest_microstructure(symbol)
    if not row:
        health = market_repo.data_health(symbol, message.from_user.id)
        collector = health.get("collector") or {}
        await message.answer(
            f"No bounded microstructure aggregate exists for {escape(symbol)}.\n"
            f"Collector: <code>{escape(str((health.get('global_source_health') or {}).get('collector') or 'NOT_STARTED'))}</code>"
            f" · last error <code>{escape(str(collector.get('last_error_code') or 'none'))}</code>."
        )
        return
    aggregate = row["aggregate"]
    walls = aggregate.get("walls") or []
    wall_lines = "\n".join(
        f"• {escape(str(item.get('side')))} <code>{float(item.get('price') or 0):.8g}</code> · "
        f"{escape(str(item.get('state')))} · persistence {float(item.get('persistence_ratio') or 0):.0%}"
        for item in walls[:6]) or "• no qualified wall interaction"
    await message.answer(
        f"<b>Order-book Research · {escape(symbol)}</b>\n\n"
        f"Status / stale: <code>{escape(str(aggregate.get('status') or 'UNKNOWN'))} / {row.get('stale')}</code>\n"
        f"Samples / interaction quality: <b>{int(aggregate.get('sample_count') or 0)} / "
        f"{float(aggregate.get('interaction_quality') or 0):.1f}</b>\n"
        f"Spread: <b>{float(aggregate.get('spread_pct') or 0):.4f}%</b>\n"
        f"Inference: <code>{escape(str(aggregate.get('absorption_inference') or 'UNCONFIRMED'))}</code>\n"
        f"History: <code>{escape(str(market_repo.microstructure_history(symbol).get('status')))}</code>\n\n"
        f"{wall_lines}\n\nResting walls remain untrusted until price interaction confirms them.", parse_mode="HTML")


@router.message(Command("data_health"))
async def data_health(message: Message):
    symbol = _symbol_argument(message)
    if not symbol:
        await message.answer("Usage: <code>/data_health SYMBOL</code>\nExample: <code>/data_health BTCUSDT</code>", parse_mode="HTML")
        return
    report = market_repo.data_health(symbol, message.from_user.id)
    global_health = report.get("global_source_health") or {}
    decision_health = report.get("decision_snapshot_availability") or {}
    lines = ["<b>Global source health</b>"] + [
        f"{escape(name.replace('_', ' ').title())}: <code>{escape(str(value))}</code>"
        for name, value in global_health.items()
    ] + ["", "<b>Decision snapshot availability</b>"] + [
        f"{escape(name.replace('_', ' ').title())}: <code>{escape(str(value))}</code>"
        for name, value in decision_health.items()
    ]
    remediation = report.get("remediation") or {}
    hints = [f"• {escape(str(value))}" for value in remediation.values()
             if value and "Wait for" not in str(value)][:3]
    await message.answer(
        f"<b>Data Health · {escape(symbol)}</b>\n\n" + "\n".join(lines) +
        ("\n\n<b>Remediation</b>\n" + "\n".join(hints) if hints else "") +
        "\n\nUnavailable inputs remain explicit and cannot be reconstructed with future data.",
        parse_mode="HTML")


async def _derivatives_context(message: Message, family: str) -> None:
    if not await _require_capability(message, "DERIVATIVES_VIEW"):
        return
    symbol = _symbol_argument(message)
    if not symbol:
        await message.answer(f"Usage: <code>/{family} SYMBOL</code>\nExample: <code>/{family} BTCUSDT</code>", parse_mode="HTML")
        return
    report = market_repo.data_health(symbol, message.from_user.id)
    key = "funding_context" if family == "funding" else "open_interest_context"
    context = report[key]
    status = report[family]
    if not context:
        await message.answer(f"{family.replace('_', ' ').title()} is <code>{status}</code> for {escape(symbol)}.",
                             parse_mode="HTML")
        return
    value_key = "funding_rate" if family == "funding" else "open_interest"
    await message.answer(
        f"<b>{family.replace('_', ' ').title()} · {escape(symbol)}</b>\n\n"
        f"Status: <code>{status}</code>\nValue: <code>{escape(str(context.get(value_key)))}</code>\n"
        f"Reported: <code>{escape(str(context.get('reported_at') or 'unknown'))}</code>\n"
        f"History: <code>{escape(str(context.get('history_status') or 'INSUFFICIENT_HISTORY'))}</code> "
        f"({int(context.get('history_points') or 0)} points)\n"
        f"Source: <code>{escape(str(context.get('source') or 'BINGX_PUBLIC_FUTURES_MARKET'))}</code>\n\n"
        "Historical deltas and extremes remain unavailable until sufficient real snapshots accumulate.",
        parse_mode="HTML")


@router.message(Command("funding"))
async def funding(message: Message):
    await _derivatives_context(message, "funding")


@router.message(Command("open_interest"))
async def open_interest(message: Message):
    await _derivatives_context(message, "open_interest")


@router.message(Command("pump_reversals"))
async def pump_reversals(message: Message):
    rows = market_repo.recent_reversals(message.from_user.id, limit=12)
    lines = []
    for row in rows:
        candidates = row.get("reversal") or {}
        active = [candidate for candidate in candidates.values() if isinstance(candidate, dict)
                  and not str(candidate.get("state") or "").endswith("_INVALID")]
        for candidate in active:
            lines.append(f"<b>#{row['signal_id']} {escape(row['symbol'])} {escape(row['timeframe'])}</b> · "
                         f"<code>{escape(str(candidate.get('state')))}</code> · "
                         f"{float(candidate.get('move_24h_pct') or 0):+.1f}%")
    await message.answer(
        "<b>Pump/Dump Reversal Research</b>\n\n" + ("\n".join(lines) or "No early/confirmed candidate snapshots yet.") +
        "\n\nSHADOW/PAPER only. Continuation risk is reported explicitly; no trade is opened.", parse_mode="HTML")


@router.message(Command("entry_research"))
async def entry_research(message: Message):
    report = market_repo.policy_report("ENTRY", message.from_user.id)
    calibration = market_repo.readiness_timing_cohorts(message.from_user.id)
    cohort_lines = [
        f"<code>{item['state']}</code> · n={item['n']} · MFE {_number(item['subsequent_mfe'], 'R')} · "
        f"MAE {_number(item['subsequent_mae'], 'R')} · missed/avoided {item['missed_winners']}/{item['avoided_losers']}"
        for item in calibration["cohorts"]
    ]
    await message.answer(
        "<b>Entry Research</b>\n\n"
        f"Decision snapshots / resolved: <b>{report['decision_snapshots']} / {report['resolved_outcomes']}</b>\n"
        f"Status: <code>{report['status']}</code>\n"
        f"Policies: <code>{escape(', '.join(report['policies']))}</code>\n\n"
        "<b>Readiness V4 timing cohorts</b>\n" + "\n".join(cohort_lines) + "\n\n"
        "Fill probability, MAE, missed winners, and avoided losses require ordered path data.", parse_mode="HTML")


@router.message(Command("reentry_research"))
async def reentry_research(message: Message):
    report = market_repo.policy_report("REENTRY", message.from_user.id)
    await message.answer(
        "<b>Re-entry Research</b>\n\n"
        f"Decision snapshots / resolved: <b>{report['decision_snapshots']} / {report['resolved_outcomes']}</b>\n"
        f"Status: <code>{report['status']}</code>\n"
        f"Maximum attempts: <b>{report['maximum_attempts']}</b>\n"
        f"Martingale: <b>{'YES' if report['martingale'] else 'NO'}</b>\n\n"
        "Each attempt requires new evidence, cooldown, a new identity, and bounded cumulative risk.", parse_mode="HTML")


@router.message(Command("quality_report"))
async def quality_report(message: Message):
    report = market_repo.quality_threshold_report(message.from_user.id)
    calibration = market_repo.quality_calibration_cohorts(message.from_user.id)
    lines = [f"<b>{item['threshold']}</b> · n={item['trades']} · E {_number(item['expectancy_r'], 'R')} · "
             f"missed W {item['missed_winners']} · avoided L {item['avoided_losses']}"
             for item in report["threshold_curves"]]
    calibration_lines = [
        f"<code>{item['bucket']}</code> · n={item['n']} · WR {_number(item['win_rate_pct'], '%', 1)} · "
        f"E {_number(item['expectancy_r'], 'R')} · PF {_number(item['profit_factor'])} · "
        f"cost-adj {_number(item['cost_adjusted_expectancy_r'], 'R')} · {item['status']}"
        for item in calibration["cohorts"]
    ]
    await message.answer(
        "<b>Quality Threshold Research</b>\n\n" + "\n".join(lines) +
        "\n\n<b>Quality calibration buckets</b>\n" + "\n".join(calibration_lines) +
        f"\n\nResolved samples: <b>{report['resolved_samples']}</b> · <code>{report['status']}</code>\n"
        "No threshold is applied automatically and no profitability claim is made.", parse_mode="HTML")


@router.message(Command("quality_cohorts"))
async def quality_cohorts(message: Message):
    report = market_repo.quality_exception_cohorts(message.from_user.id)
    lines = [f"<b>{escape(item['name'].replace('_', ' ').title())}</b>: n={item['samples']} · "
             f"avg {_number(item['average_r'], 'R')}"
             for item in report["cohorts"]]
    await message.answer(
        "<b>Quality Exception Cohorts</b>\n\n" + "\n".join(lines) +
        "\n\nCohorts preserve low-quality winners and high-quality losers for calibration. "
        "Individual anecdotes never change policy automatically.")
