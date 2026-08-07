from __future__ import annotations

import json
import os
from html import escape

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from database.database import connect
from services.ai_evaluation import AIEvaluationService
from services.ai_trading import (
    AIDecisionRepository, AITradingMode, build_ai_provider, configured_ai_mode, set_user_ai_mode,
)
from services.ai_operations import (
    AIConfigurationValidator, AIControlRepository, AIGovernanceState, AIProviderCertificationService,
    promotion_evidence, provider_identity,
)
from services.ai_intelligence import AIObservationIntelligence


router = Router()


def _admin_ids() -> set[int]:
    raw = os.getenv("ADMIN_IDS", os.getenv("ADMIN_ID", ""))
    return {int(value.strip()) for value in raw.replace(";", ",").split(",") if value.strip().isdigit()}


def _is_admin(message: Message) -> bool:
    return bool(message.from_user and message.from_user.id in _admin_ids())


def _items(raw: str | None, limit: int = 4) -> str:
    try:
        values = json.loads(raw or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        values = []
    return "\n".join(f"• {escape(str(value))}" for value in values[:limit]) or "• none recorded"


def _values(raw: str | None) -> list:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return value if isinstance(value, list) else []


def format_ai_decision(row: dict) -> str:
    warning = "Raw AI confidence is advisory, not a calibrated probability."
    return (
        f"🤖 <b>AI shadow decision</b>\n\n"
        f"Signal: <code>{row['signal_id']}</code> · {escape(str(row['symbol']))} {escape(str(row['timeframe']))}\n"
        f"Mode: <code>{escape(str(row['requested_mode']))}</code>\n"
        f"Provider/model: <code>{escape(str(row['provider']))} / {escape(str(row.get('model') or 'none'))}</code>\n"
        f"Protocol: <code>{escape(str(row.get('provider_protocol') or 'legacy'))}</code>\n"
        f"Output: <code>{escape(str(row.get('requested_output_mode') or 'legacy'))} → {escape(str(row.get('effective_output_mode') or 'legacy'))}</code>\n"
        f"Schema: <code>{escape(str(row.get('schema_version') or 'legacy'))}</code>\n"
        f"Action: <b>{escape(str(row['recommended_action']))}</b> · risk <code>{float(row['recommended_risk_multiplier']):.2f}x</code>\n"
        f"Confidence / uncertainty: <code>{float(row['raw_confidence']):.1f} / {float(row['uncertainty']):.1f}</code>\n"
        f"Opportunity quality: <code>{float(row.get('opportunity_quality') or 0):.1f}</code>\n"
        f"Regime / direction: <code>{escape(str(row['regime']))} / {escape(str(row['direction']))}</code>\n"
        f"Regime tags: <code>{escape(', '.join(str(value) for value in _values(row.get('regime_tags_json'))) or 'UNKNOWN')}</code>\n"
        f"Validation: <code>{escape(str(row.get('validation_stage') or 'legacy'))} / {escape(str(row['validation_code']))}</code>\n"
        f"Cost: <code>{escape(str(row.get('cost_status') or 'UNPRICED'))} · ${escape(str(row.get('estimated_cost_usd') or 0))}</code>\n\n"
        f"<b>Supports</b>\n{_items(row.get('supporting_factors_json'))}\n\n"
        f"<b>Conflicts</b>\n{_items(row.get('conflicting_factors_json'))}\n\n"
        f"Uncertainty: {escape(str(row.get('uncertainty_explanation') or 'not recorded'))}\n"
        f"Explanation: {escape(str(row['explanation']))}\n\n<i>{warning}</i>"
    )


def _signal_id(message: Message) -> int | None:
    parts = (message.text or "").split()
    return int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None


def _provider_snapshot(telegram_id: int | None = None) -> dict:
    provider = build_ai_provider()
    identity = provider_identity(provider)
    validation = AIConfigurationValidator().validate(provider)
    controls = AIControlRepository()
    certification = controls.certification_state(provider, validation)
    current = AIEvaluationService().metrics(telegram_id, identity["identity_checksum"])
    global_history = AIEvaluationService().metrics(telegram_id)
    governance = controls.governance_state(identity["provider"], identity["identity_checksum"])
    blockers = list(validation.errors)
    if certification["state"] != "PASSED":
        blockers.append(f"CERTIFICATION_{certification['state']}")
    if controls.kill_status().get("enabled"):
        blockers.append("GLOBAL_AI_KILL_SWITCH")
    try:
        if current["decision_count"] < int(os.getenv("AI_PROMOTION_MIN_SHADOW_DECISIONS", "30")):
            blockers.append("CURRENT_IDENTITY_SAMPLE_MINIMUM")
        if current["valid_schema_rate"] < float(os.getenv("AI_PROMOTION_MIN_SCHEMA_VALID_RATE", "0.99")):
            blockers.append("SCHEMA_VALID_RATE_BELOW_MINIMUM")
        if current["semantic_valid_rate"] < float(os.getenv("AI_PROMOTION_MIN_SEMANTIC_VALID_RATE", "0.95")):
            blockers.append("SEMANTIC_VALID_RATE_BELOW_MINIMUM")
        if current["transport_failure_rate"] > float(os.getenv("AI_PROMOTION_MAX_TRANSPORT_FAILURE_RATE", "0.05")):
            blockers.append("TRANSPORT_FAILURE_RATE_ABOVE_MAXIMUM")
    except ValueError:
        blockers.append("PROMOTION_THRESHOLD_CONFIG_INVALID")
    return {"provider": provider, "identity": identity, "validation": validation,
            "certification": certification, "current": current, "global": global_history,
            "governance": governance, "blockers": list(dict.fromkeys(blockers))}


@router.message(Command("ai_status"))
async def ai_status(message: Message) -> None:
    user_id = message.from_user.id
    mode = configured_ai_mode(user_id)
    snapshot = _provider_snapshot(user_id)
    metrics, global_metrics = snapshot["current"], snapshot["global"]
    identity = snapshot["identity"]
    latest = AIDecisionRepository().latest(telegram_id=user_id)
    state_key = f"{snapshot['provider'].name}:{identity['identity_checksum']}"
    with connect() as conn:
        state = conn.execute("SELECT * FROM ai_provider_state WHERE provider=? AND identity_checksum=?",
            (state_key, identity["identity_checksum"])).fetchone()
    health = state["state"] if state else ("DISABLED" if os.getenv("AI_PROVIDER", "disabled") == "disabled" else "UNKNOWN")
    try:
        provider = snapshot["provider"]
        capabilities = provider.capabilities
        protocol = provider.protocol
        strict = capabilities.supports_json_schema and capabilities.supports_strict_schema
    except Exception:
        protocol, strict = "INVALID", False
    await message.answer(
        f"🤖 <b>AI intelligence status</b>\n\nMode: <code>{mode.value}</code>\n"
        f"Provider/model: <code>{escape(os.getenv('AI_PROVIDER','disabled'))} / {escape(os.getenv('AI_MODEL','unset') or 'unset')}</code>\n"
        f"Protocol / strict schema: <code>{escape(protocol)} / {'YES' if strict else 'NO'}</code>\n"
        f"Identity: <code>{identity['identity_checksum'][:16]}</code>\n"
        f"Certification / governance: <b>{snapshot['certification']['state']} / {snapshot['governance']}</b>\n"
        f"Health: <b>{escape(str(health))}</b>\nCurrent identity decisions: <b>{metrics['decision_count']}</b>\n"
        f"Global history decisions: <b>{global_metrics['decision_count']}</b>\n"
        f"Abstention: <code>{metrics['abstention_rate']:.1%}</code>\n"
        f"Schema / semantic valid: <code>{metrics['valid_schema_rate']:.1%} / {metrics['semantic_valid_rate']:.1%}</code>\n"
        f"Downgrades: <b>{metrics['downgrade_count']}</b>\n"
        f"Average latency: <code>{metrics['average_latency_ms']:.1f} ms</code>\n"
        f"Cost: <code>${escape(metrics['estimated_cost_usd'])}</code>\n"
        f"Request IDs available: <b>{'YES' if metrics['request_ids_available'] else 'NO'}</b>\n"
        f"Activation blockers: <code>{escape(', '.join(snapshot['blockers']) or 'none')}</code>\n"
        f"Latest: <code>{escape(str(latest['recommended_action'])) if latest else 'none'}</code>\n\n"
        "AI is advisory and cannot place, modify, or close orders.")


@router.message(Command("ai_mode"))
async def ai_mode(message: Message) -> None:
    parts = (message.text or "").split()
    if len(parts) == 1:
        await message.answer(f"AI mode: <code>{configured_ai_mode(message.from_user.id).value}</code>\nUse /ai_mode AI_OFF|AI_OBSERVE|AI_SHADOW|AI_ASSIST. AI_GATED is unavailable.")
        return
    try:
        requested = AITradingMode(parts[1].upper())
    except ValueError:
        await message.answer("Unknown AI mode. Use AI_OFF, AI_OBSERVE, AI_SHADOW, or AI_ASSIST.")
        return
    effective = set_user_ai_mode(message.from_user.id, requested)
    suffix = " AI_GATED is fail-closed in v9.9.18." if requested is AITradingMode.AI_GATED else ""
    await message.answer(f"AI mode set to <code>{effective.value}</code>.{suffix}")


@router.message(Command("ai_disable"))
async def ai_disable(message: Message) -> None:
    set_user_ai_mode(message.from_user.id, AITradingMode.AI_OFF)
    await message.answer("AI analysis is disabled for your account. Deterministic trading behavior is unchanged.")


@router.message(Command("ai_decision"))
@router.message(Command("ai_explain"))
async def ai_decision(message: Message) -> None:
    row = AIDecisionRepository().latest(telegram_id=message.from_user.id, signal_id=_signal_id(message))
    await message.answer(format_ai_decision(row) if row else "No AI decision was found for that signal.")


@router.message(Command("ai_metrics"))
@router.message(Command("ai_compare"))
@router.message(Command("ai_quality"))
async def ai_metrics(message: Message) -> None:
    snapshot = _provider_snapshot(message.from_user.id)
    identity = snapshot["identity"]
    quality = AIEvaluationService().quality_report(
        message.from_user.id, identity_checksum=identity["identity_checksum"])
    metrics = quality["metrics"]
    global_metrics = snapshot["global"]
    calibration = metrics["calibration"]
    window = quality["rolling"]["24h"]
    distribution = ", ".join(f"{escape(k)}={v}" for k, v in sorted(metrics["recommendations"].items())) or "none"
    await message.answer(
        f"📊 <b>AI shadow metrics</b>\n\n<b>CURRENT PROVIDER IDENTITY</b> <code>{identity['identity_checksum'][:16]}</code>\n"
        f"Decisions: <b>{metrics['decision_count']}</b>\n"
        f"Valid schema: <code>{metrics['valid_schema_rate']:.1%}</code>\n"
        f"Semantic valid: <code>{metrics['semantic_valid_rate']:.1%}</code>\n"
        f"Abstention: <code>{metrics['abstention_rate']:.1%}</code>\nDistribution: <code>{distribution}</code>\n"
        f"Agreement: <code>{metrics['agreement_rate'] if metrics['agreement_rate'] is not None else 'insufficient data'}</code>\n"
        f"Downgrades / cost: <code>{metrics['downgrade_count']} / {escape(metrics['cost_status'])}</code>\n"
        f"Brier / ECE: <code>{calibration['brier_score']} / {calibration['expected_calibration_error']}</code>\n"
        f"Resolved calibration samples: <b>{calibration['sample_size']}</b>\n"
        f"24h requests / p95: <code>{window['requests']} / {window['latency_ms']['p95']:.1f} ms</code>\n"
        f"Transport failures / timeouts / 429 / 5xx: <code>{metrics['transport_failure_count']} / {metrics['timeout_count']} / {metrics['rate_limit_count']} / {metrics['server_error_count']}</code>\n"
        f"Duplicates / queue drops: <code>{metrics['duplicates_suppressed']} / {metrics['queue_drops']}</code>\n"
        f"Accept / reject / abstain cohorts: <code>{quality['counterfactual']['ai_accept']['sample_size']} / "
        f"{quality['counterfactual']['ai_reject']['sample_size']} / {quality['counterfactual']['ai_abstain']['sample_size']}</code>\n\n"
        f"<b>GLOBAL HISTORY</b> decisions / cost: <code>{global_metrics['decision_count']} / ${escape(global_metrics['estimated_cost_usd'])}</code>\n\n"
        "No improvement claim is made without sufficient out-of-sample evidence.")


def _metric(value, *, percent: bool = False) -> str:
    if value is None:
        return "insufficient data"
    return f"{float(value):.1%}" if percent else f"{float(value):.4f}"


@router.message(Command("ai_dashboard"))
async def ai_dashboard(message: Message) -> None:
    identity = provider_identity(build_ai_provider())
    report = AIObservationIntelligence().dashboard(identity["identity_checksum"], message.from_user.id)
    counter = report["counterfactual"]
    health = report["health"]
    queue = health.get("queue") or {}
    await message.answer(
        f"📡 <b>AI observation dashboard</b>\n\n"
        f"Identity: <code>{identity['identity_checksum'][:16]}</code>\n"
        f"Decisions / provider calls: <code>{report['decisions']} / {report['provider_requests']}</code>\n"
        f"Valid / cache hit ratio: <code>{report['valid_rate']:.1%} / {report['cache_hit_ratio']:.1%}</code>\n"
        f"Resolved counterfactuals: <code>{counter['sample_size']}</code>\n"
        f"GPT expectancy / precision / recall: <code>{_metric(counter['gpt_expectancy_r'])} / "
        f"{_metric(counter['precision'], percent=True)} / {_metric(counter['recall'], percent=True)}</code>\n"
        f"Recent provider failures / retries / cancellations: <code>{health['failures']} / {health['retries']} / {health['cancellations']}</code>\n"
        f"Provider p95: <code>{health['p95_latency_ms']:.1f} ms</code>\n"
        f"Last queue queued / dropped / failed: <code>{queue.get('queued', 0)} / {queue.get('dropped', 0)} / {queue.get('failed', 0)}</code>\n\n"
        "All observations are advisory and have zero execution authority.")


@router.message(Command("ai_history"))
async def ai_history(message: Message) -> None:
    parts = (message.text or "").split()
    limit = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 10
    rows = AIObservationIntelligence.history(message.from_user.id, limit)
    lines = []
    for row in rows:
        outcome = row.get("classification") or "OPEN"
        tags = ",".join(str(value) for value in _values(row.get("market_regimes_json") or row.get("regime_tags_json")))
        lines.append(
            f"• <code>{row['signal_id']}</code> {escape(str(row['symbol']))} {escape(str(row['timeframe']))} · "
            f"<b>{escape(str(row['recommended_action']))}</b> · Q {float(row.get('opportunity_quality') or 0):.0f} · "
            f"{escape(tags or str(row.get('regime') or 'UNKNOWN'))} · {escape(str(outcome))}")
    await message.answer("🧾 <b>AI observation history</b>\n\n" + ("\n".join(lines) if lines else "No observations found."))


@router.message(Command("ai_regimes"))
async def ai_regimes(message: Message) -> None:
    identity = provider_identity(build_ai_provider())
    report = AIObservationIntelligence().regime_report(identity["identity_checksum"], message.from_user.id)
    lines = [
        f"• {escape(name)}: samples <code>{data['samples']}</code> · accuracy <code>{data['accuracy']:.1%}</code> · "
        f"expectancy <code>{data['expectancy_r']:.3f}R</code> · FP/FN <code>{data['false_positives']}/{data['false_negatives']}</code>"
        for name, data in sorted(report["regimes"].items())
    ]
    await message.answer("🧭 <b>AI regime intelligence</b>\n\n" +
                         ("\n".join(lines) if lines else "No resolved regime samples yet."))


@router.message(Command("ai_similarity"))
async def ai_similarity(message: Message) -> None:
    signal_id = _signal_id(message)
    if signal_id is None:
        await message.answer("Use <code>/ai_similarity &lt;signal_id&gt;</code>.")
        return
    rows = AIObservationIntelligence.similarities(signal_id, message.from_user.id)
    lines = [
        f"• signal <code>{row['similar_signal_id']}</code> · score <code>{float(row['similarity_score']):.1f}%</code> · "
        f"outcome <code>{_metric(row.get('outcome_r'))}R</code>"
        for row in rows
    ]
    await message.answer(f"🧩 <b>Similar observations for {signal_id}</b>\n\n" +
                         ("\n".join(lines) if lines else "No sufficiently similar history found."))


@router.message(Command("ai_learning"))
async def ai_learning(message: Message) -> None:
    identity = provider_identity(build_ai_provider())
    report = AIObservationIntelligence.latest_learning(identity["identity_checksum"])
    if not report:
        await message.answer("No advisory learning snapshot exists yet.")
        return
    evidence = report.get("evidence_json") or []
    failures = report.get("recurring_failures_json") or []
    evidence_lines = "\n".join(
        f"• {escape(str(item['factor']))}: <code>{item['samples']} / {float(item['expectancy_r']):.3f}R</code>"
        for item in evidence[:5]) or "• insufficient samples"
    failure_lines = "\n".join(
        f"• {escape(str(item['factor']))}: <code>{float(item['expectancy_r']):.3f}R</code>"
        for item in failures[:5]) or "• none established"
    await message.answer(
        f"🧠 <b>Advisory learning</b>\n\nSamples: <code>{report['sample_size']}</code>\n"
        f"Snapshot: <code>{escape(str(report['snapshot_key'])[:16])}</code>\n\n"
        f"<b>Repeated evidence</b>\n{evidence_lines}\n\n<b>Recurring failures</b>\n{failure_lines}\n\n"
        "Learning changes future observation context only; it cannot change deterministic execution.")


@router.message(Command("ai_statistics"))
@router.message(Command("ai_counterfactual"))
async def ai_counterfactual(message: Message) -> None:
    identity = provider_identity(build_ai_provider())
    report = AIObservationIntelligence().counterfactual_report(identity["identity_checksum"], message.from_user.id)
    await message.answer(
        f"⚖️ <b>AI counterfactual statistics</b>\n\nSamples / status: <code>{report['sample_size']} / {report['status']}</code>\n"
        f"Precision / recall: <code>{_metric(report['precision'], percent=True)} / {_metric(report['recall'], percent=True)}</code>\n"
        f"False positives / negatives: <code>{report['false_positives']} / {report['false_negatives']}</code>\n"
        f"GPT / deterministic expectancy: <code>{_metric(report['gpt_expectancy_r'])}R / {_metric(report['deterministic_expectancy_r'])}R</code>\n"
        f"Abstention quality: <code>{_metric(report['abstention_quality'], percent=True)}</code>\n"
        f"Disagreements / profitability: <code>{report['disagreement_count']} / {_metric(report['disagreement_profitability_r'])}R</code>\n"
        f"Opportunity quality, profitable / unprofitable: <code>{_metric(report['opportunity_quality_positive'])} / {_metric(report['opportunity_quality_negative'])}</code>\n\n"
        "No performance claim is made while samples are insufficient.")


@router.message(Command("ai_provider_health"))
async def ai_provider_health(message: Message) -> None:
    identity = provider_identity(build_ai_provider())
    report = AIObservationIntelligence().provider_health(identity["identity_checksum"])
    state = report.get("circuit") or {}
    queue = report.get("queue") or {}
    await message.answer(
        f"🩺 <b>AI provider health</b>\n\nCircuit: <code>{escape(str(state.get('state') or 'UNKNOWN'))}</code>\n"
        f"Consecutive / total failures: <code>{state.get('consecutive_failures', 0)} / {state.get('total_failures', 0)}</code>\n"
        f"Total requests / retries: <code>{state.get('total_requests', 0)} / {state.get('total_retries', 0)}</code>\n"
        f"Recent events / failures / cancellations: <code>{report['recent_events']} / {report['failures']} / {report['cancellations']}</code>\n"
        f"p95 latency: <code>{report['p95_latency_ms']:.1f} ms</code>\n"
        f"Queue processed / dropped / failed: <code>{queue.get('processed', 0)} / {queue.get('dropped', 0)} / {queue.get('failed', 0)}</code>\n"
        f"Last error: <code>{escape(str(state.get('last_error_code') or 'none'))}</code>")


@router.message(Command("ai_cost"))
async def ai_cost(message: Message) -> None:
    snapshot = _provider_snapshot(message.from_user.id)
    metrics, global_metrics = snapshot["current"], snapshot["global"]
    await message.answer(
        f"<b>CURRENT PROVIDER IDENTITY</b> <code>{snapshot['identity']['identity_checksum'][:16]}</code>\n"
        f"Tokens input / cached / cache-write / output / reasoning: <code>{metrics['input_tokens']} / {metrics['cached_tokens']} / {metrics['cache_write_tokens']} / {metrics['output_tokens']} / {metrics['reasoning_tokens']}</code>\n"
        f"Cost: <code>${escape(metrics['estimated_cost_usd'])}</code> · status <b>{escape(metrics['cost_status'])}</b> · requests <b>{metrics['decision_count']}</b>\n\n"
        f"<b>GLOBAL HISTORY</b>\nCost: <code>${escape(global_metrics['estimated_cost_usd'])}</code> · requests <b>{global_metrics['decision_count']}</b>")


@router.message(Command("ai_provider"))
async def ai_provider(message: Message) -> None:
    snapshot = _provider_snapshot()
    identity, validation = snapshot["identity"], snapshot["validation"]
    certification, metrics = snapshot["certification"], snapshot["current"]
    controls = AIControlRepository()
    kill = controls.kill_status()
    row = certification.get("row") or {}
    await message.answer(
        f"🤖 <b>AI provider</b>\n\nProvider / protocol: <code>{escape(identity['provider'])} / {escape(identity['protocol'])}</code>\n"
        f"Endpoint: <code>{escape(identity['endpoint'])}</code>\nModel: <code>{escape(identity['model'])}</code>\n"
        f"Identity: <code>{identity['identity_checksum'][:16]}</code>\nConfiguration: <b>{'VALID' if validation.valid else 'INVALID'}</b>\n"
        f"Output / reasoning: <code>{identity['effective_output_mode']} / {identity['reasoning_effort'] or 'none'}</code>\n"
        f"Certification: <b>{certification['state']}</b> · expiry <code>{escape(str(row.get('expires_at') or 'none'))}</code>\n"
        f"Governance: <b>{snapshot['governance']}</b>\n"
        f"Current identity decisions / schema / semantic: <code>{metrics['decision_count']} / {metrics['valid_schema_rate']:.1%} / {metrics['semantic_valid_rate']:.1%}</code>\n"
        f"Current cost / request IDs: <code>${escape(metrics['estimated_cost_usd'])} / {'YES' if metrics['request_ids_available'] else 'NO'}</code>\n"
        f"Global kill switch: <b>{'ON' if kill['enabled'] else 'OFF'}</b>\n"
        f"Activation/promotion blockers: <code>{escape(', '.join(snapshot['blockers']) or 'none')}</code>\n\nAI remains advisory-only.")


@router.message(Command("ai_certification"))
async def ai_certification(message: Message) -> None:
    if not _is_admin(message):
        await message.answer("⛔ Admin command.")
        return
    parts = (message.text or "").split()
    provider = build_ai_provider()
    identity = provider_identity(provider)
    if len(parts) < 2:
        summary = AIControlRepository().certification_state(provider)
        row = summary.get("row") or {}
        await message.answer(
            f"Certification: <b>{summary['state']}</b>\n"
            f"Identity: <code>{identity['identity_checksum'][:16]}</code>\n"
            f"Started/completed: <code>{escape(str(row.get('started_at') or 'never'))} / {escape(str(row.get('completed_at') or 'never'))}</code>\n"
            f"Expires: <code>{escape(str(row.get('expires_at') or 'none'))}</code>\n"
            f"Validation: <code>{escape(str(row.get('validation_stage') or 'none'))} / {escape(str(row.get('validation_code') or 'none'))}</code>\n"
            f"Completion / extraction: <code>{escape(str(row.get('provider_completion_status') or 'none'))} / {escape(str(row.get('extraction_path') or 'none'))}</code>\n"
            f"Incomplete reason: <code>{escape(str(row.get('provider_incomplete_reason') or 'none'))}</code>\n"
            "Run <code>/ai_certification run</code> to make one paid provider request.")
        return
    action = parts[1].lower()
    if action == "run":
        await message.answer("⚠️ If configuration is valid, certification now contacts the real provider, makes one bounded request, and incurs API usage. Repeated runs are temporarily suppressed.")
        report = await AIProviderCertificationService(provider).certify(message.from_user.id)
        if report.get("duplicate_suppressed"):
            await message.answer(
                f"Certification: <b>{report['status']}</b>\nFailure: <code>{escape(report['failure_code'])}</code>\n"
                "No provider request was made and no additional API charge was incurred.")
            return
        await message.answer(
            f"Certification: <b>{report['status']}</b>\n"
            f"Failure: <code>{escape(report['failure_code'] or 'none')}</code>\n"
            f"Stage: <code>{escape(report.get('validation_stage') or 'none')}</code>\n"
            f"Completion: <code>{escape(str(report['checks'].get('completion_status') or 'none'))}</code>\n"
            f"Extraction: <code>{escape(str(report['checks'].get('extraction_path') or 'none'))}</code>\n"
            f"Incomplete reason: <code>{escape(str(report['checks'].get('incomplete_reason') or 'none'))}</code>\n"
            f"Request ID available: <b>{'YES' if report['checks'].get('request_id_available') else 'NO'}</b>\n"
            f"Usage/cost: <code>{'PASS' if report['checks'].get('usage_reporting') else 'FAIL'} / {'PASS' if report['checks'].get('cost_parsed') else 'FAIL'}</code>\n"
            f"Expires: <code>{escape(str(report['expires_at'] or 'none'))}</code>")
        return
    controls = AIControlRepository()
    certification = controls.certification(identity["identity_checksum"])
    if action in {"suspend", "retire"}:
        target = AIGovernanceState.SUSPENDED if action == "suspend" else AIGovernanceState.RETIRED
        controls.transition(identity["provider"], identity["identity_checksum"], target,
                            f"ADMIN_{target.value}", message.from_user.id)
        await message.answer(f"Provider governance set to <b>{target.value}</b>.")
        return
    if action == "promote" and len(parts) > 2 and parts[2].lower() in {"shadow", "assist"}:
        if not certification:
            await message.answer("Promotion blocked: <code>VALID_CERTIFICATION_REQUIRED</code>")
            return
        target = AIGovernanceState.SHADOW_CERTIFIED if parts[2].lower() == "shadow" else AIGovernanceState.ASSIST_CERTIFIED
        evidence = promotion_evidence(provider, target)
        if not evidence["eligible"]:
            await message.answer(
                f"Promotion blocked: <code>{escape(', '.join(evidence['blockers']))}</code>\n"
                f"Current identity decisions: <code>{evidence['metrics']['decision_count']}</code>.")
            return
        controls.transition(identity["provider"], identity["identity_checksum"], target,
                            "EVIDENCE_REVIEW_PASSED", message.from_user.id,
                            evidence["evidence"])
        await message.answer(f"Provider promoted to <b>{target.value}</b>.")
        return
    await message.answer("Use <code>/ai_certification run|promote shadow|promote assist|suspend|retire</code>.")


@router.message(Command("ai_drift"))
async def ai_drift(message: Message) -> None:
    parts = (message.text or "").split()
    if len(parts) > 1 and parts[1].lower() == "baseline":
        if not _is_admin(message):
            await message.answer("⛔ Admin command.")
            return
        identity = provider_identity(build_ai_provider())
        captured = AIEvaluationService().capture_drift_baseline(identity["identity_checksum"], message.from_user.id)
        await message.answer(f"AI drift baseline: <b>{captured['status']}</b> · samples <code>{captured['sample_size']}</code>")
        return
    identity = provider_identity(build_ai_provider())
    report = AIEvaluationService().drift(
        message.from_user.id, identity_checksum=identity["identity_checksum"])
    alerts = ", ".join(item["metric"] for item in report["alerts"]) or "none"
    await message.answer(f"AI drift: <b>{report['status']}</b>\n24h / baseline samples: <code>{report['current_samples']} / {report['baseline_samples']}</code>\nAlerts: <code>{escape(alerts)}</code>")


@router.message(Command("ai_experiments"))
async def ai_experiments(message: Message) -> None:
    with connect() as conn:
        rows = conn.execute("SELECT name,status,variants_json FROM ai_experiments ORDER BY id DESC LIMIT 10").fetchall()
    lines = [f"• {escape(row['name'])}: <b>{escape(row['status'])}</b> <code>{escape(row['variants_json'])}</code>" for row in rows]
    await message.answer("🧪 <b>AI shadow experiments</b>\n\n" + ("\n".join(lines) if lines else "No experiments configured."))


@router.message(Command("ai_kill"))
async def ai_kill(message: Message) -> None:
    if not _is_admin(message):
        await message.answer("⛔ Admin command.")
        return
    parts = (message.text or "").split()
    controls = AIControlRepository()
    if len(parts) == 1 or parts[1].lower() == "status":
        state = controls.kill_status()
    elif parts[1].lower() in {"on", "off"}:
        state = controls.set_kill(parts[1].lower() == "on", actor_telegram_id=message.from_user.id,
                                  reason_code="TELEGRAM_ADMIN_KILL")
    else:
        await message.answer("Use <code>/ai_kill status|on|off</code>.")
        return
    await message.answer(f"Global AI kill switch: <b>{'ON' if state['enabled'] else 'OFF'}</b>\nUpdated: <code>{escape(str(state['updated_at'] or 'never'))}</code>")
