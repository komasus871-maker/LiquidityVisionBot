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
    AIDecisionRepository, AITradingMode, configured_ai_mode, set_user_ai_mode,
)


router = Router()


def _items(raw: str | None, limit: int = 4) -> str:
    try:
        values = json.loads(raw or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        values = []
    return "\n".join(f"• {escape(str(value))}" for value in values[:limit]) or "• none recorded"


def format_ai_decision(row: dict) -> str:
    warning = "Raw AI confidence is advisory, not a calibrated probability."
    return (
        f"🤖 <b>AI shadow decision</b>\n\n"
        f"Signal: <code>{row['signal_id']}</code> · {escape(str(row['symbol']))} {escape(str(row['timeframe']))}\n"
        f"Mode: <code>{escape(str(row['requested_mode']))}</code>\n"
        f"Provider/model: <code>{escape(str(row['provider']))} / {escape(str(row.get('model') or 'none'))}</code>\n"
        f"Action: <b>{escape(str(row['recommended_action']))}</b> · risk <code>{float(row['recommended_risk_multiplier']):.2f}x</code>\n"
        f"Confidence / uncertainty: <code>{float(row['raw_confidence']):.1f} / {float(row['uncertainty']):.1f}</code>\n"
        f"Regime / direction: <code>{escape(str(row['regime']))} / {escape(str(row['direction']))}</code>\n"
        f"Validation: <code>{escape(str(row['validation_code']))}</code>\n\n"
        f"<b>Supports</b>\n{_items(row.get('supporting_factors_json'))}\n\n"
        f"<b>Conflicts</b>\n{_items(row.get('conflicting_factors_json'))}\n\n"
        f"Explanation: {escape(str(row['explanation']))}\n\n<i>{warning}</i>"
    )


def _signal_id(message: Message) -> int | None:
    parts = (message.text or "").split()
    return int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None


@router.message(Command("ai_status"))
async def ai_status(message: Message) -> None:
    user_id = message.from_user.id
    mode = configured_ai_mode(user_id)
    metrics = AIEvaluationService().metrics(user_id)
    latest = AIDecisionRepository().latest(telegram_id=user_id)
    with connect() as conn:
        state = conn.execute("SELECT * FROM ai_provider_state WHERE provider=?", (os.getenv("AI_PROVIDER", "disabled"),)).fetchone()
    health = state["state"] if state else ("DISABLED" if os.getenv("AI_PROVIDER", "disabled") == "disabled" else "UNKNOWN")
    await message.answer(
        f"🤖 <b>AI intelligence status</b>\n\nMode: <code>{mode.value}</code>\n"
        f"Provider/model: <code>{escape(os.getenv('AI_PROVIDER','disabled'))} / {escape(os.getenv('AI_MODEL','unset') or 'unset')}</code>\n"
        f"Health: <b>{escape(str(health))}</b>\nDecisions: <b>{metrics['decision_count']}</b>\n"
        f"Abstention: <code>{metrics['abstention_rate']:.1%}</code>\n"
        f"Average latency: <code>{metrics['average_latency_ms']:.1f} ms</code>\n"
        f"Cost: <code>${escape(metrics['estimated_cost_usd'])}</code>\n"
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
    suffix = " AI_GATED is fail-closed in v9.9.12." if requested is AITradingMode.AI_GATED else ""
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
async def ai_metrics(message: Message) -> None:
    metrics = AIEvaluationService().metrics(message.from_user.id)
    calibration = metrics["calibration"]
    distribution = ", ".join(f"{escape(k)}={v}" for k, v in sorted(metrics["recommendations"].items())) or "none"
    await message.answer(
        f"📊 <b>AI shadow metrics</b>\n\nDecisions: <b>{metrics['decision_count']}</b>\n"
        f"Valid schema: <code>{metrics['valid_schema_rate']:.1%}</code>\n"
        f"Abstention: <code>{metrics['abstention_rate']:.1%}</code>\nDistribution: <code>{distribution}</code>\n"
        f"Agreement: <code>{metrics['agreement_rate'] if metrics['agreement_rate'] is not None else 'insufficient data'}</code>\n"
        f"Brier / ECE: <code>{calibration['brier_score']} / {calibration['expected_calibration_error']}</code>\n"
        f"Resolved calibration samples: <b>{calibration['sample_size']}</b>\n\n"
        "No improvement claim is made without sufficient out-of-sample evidence.")


@router.message(Command("ai_cost"))
async def ai_cost(message: Message) -> None:
    metrics = AIEvaluationService().metrics(message.from_user.id)
    await message.answer(f"AI recorded cost: <code>${escape(metrics['estimated_cost_usd'])}</code> across <b>{metrics['decision_count']}</b> decisions.")
