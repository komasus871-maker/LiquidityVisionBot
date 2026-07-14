from __future__ import annotations

import html
import json
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from database.observation_history import ObservationHistory
from database.candidate_history import CandidateHistory
from database.signal_history import SignalHistory
from utils.price import fmt_price

router = Router()
history = SignalHistory()
observations = ObservationHistory()
candidates = CandidateHistory()


def _status_icon(status: str) -> str:
    return {
        "WATCHING": "👀", "TRIGGERED": "🔔", "ACTIVE": "⚡", "TP1": "🎯", "TP2": "🏆",
        "TP3": "👑", "STOP": "🛑", "BREAKEVEN": "🛡", "INVALIDATED": "⚠️", "EXPIRED": "⌛",
    }.get(status, "•")


def _duration(started_at: str | None, ended_at: str | None = None) -> str:
    if not started_at:
        return "—"
    try:
        start = datetime.fromisoformat(started_at)
        end = datetime.fromisoformat(ended_at) if ended_at else datetime.now(timezone.utc)
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        seconds = max(0, int((end - start).total_seconds()))
    except (ValueError, TypeError):
        return "—"
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    return f"{hours}h {minutes}m" if hours else f"{minutes}m"


def _event_details(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}


@router.message(F.text == "📒 Journal")
async def journal_handler(message: Message):
    user_id = message.from_user.id
    stats = history.get_stats(user_id)
    recent = history.get_recent(user_id, limit=8)
    obs_recent = observations.recent(user_id, limit=5)
    observation_count = observations.count(user_id)
    candidate_recent = candidates.recent(user_id, limit=5)

    recent_text = []
    for item in recent:
        current = item.get("current_price") if item.get("current_price") is not None else item["entry"]
        if item["status"] in {"WATCHING", "TRIGGERED"}:
            metrics = (
                f"Pre-MFE {float(item.get('pre_activation_max_profit_pct') or 0):+.2f}% | "
                f"Pre-MAE {float(item.get('pre_activation_max_drawdown_pct') or 0):+.2f}%"
            )
        else:
            metrics = (
                f"MFE {float(item.get('max_profit_pct') or 0):+.2f}% | "
                f"MAE {float(item.get('max_drawdown_pct') or 0):+.2f}%"
            )
        intelligence = ""
        if item["status"] in {"ACTIVE", "TP1", "TP2"}:
            health = html.escape(str(item.get("trade_health") or "🟡 STABLE"))
            confidence = float(item.get("dynamic_confidence") or item.get("confidence") or 0)
            intelligence = f"\n   {health} · Confidence {confidence:.0f}%"
        recent_text.append(
            f"{_status_icon(item['status'])} <b>#{item['id']} {item['symbol']} {item['side']}</b> — {item['status']}\n"
            f"   Entry {fmt_price(item['entry'])} → {fmt_price(current)} | {metrics}{intelligence}\n"
            f"   /trade {item['id']}"
        )

    timeline = "\n\n".join(recent_text) if recent_text else "Пока нет сохранённых торговых сетапов."
    obs_text = "\n\n".join(
        f"👁 <b>#{x['id']} {x['symbol']} {x['direction']}</b> — {x['execution_status']}\n"
        f"   Direction {x['direction_score']:.1f} | Ready {x['readiness']:.1f} | Price {fmt_price(x['price'])}"
        for x in obs_recent
    ) or "Пока нет аналитических наблюдений."
    candidate_text = "\n\n".join(
        f"🧩 <b>#{x['id']} {x['symbol']} {x['side']}</b> — CANDIDATE\n"
        f"   Заблокирован активной сделкой #{x.get('blocked_by_signal_id') or '—'}"
        for x in candidate_recent
    ) or "Нет ожидающих альтернативных сценариев."

    await message.answer(f"""
📒 <b>Trade Journal PRO</b>

👀 Watching: {stats.get('watching_count') or 0}
🔔 Triggered: {stats.get('triggered_count') or 0}
⚡ Active: {stats.get('active_count') or 0}
✅ Closed: {stats.get('closed_count') or 0}
📚 Total tracked: {stats.get('total') or 0}
👁 Observations: {observation_count}

🏆 Win rate by TP1: {stats.get('win_rate') or 0}%
🎯 TP1 rate: {stats.get('tp1_rate') or 0}%
🎯 TP2 rate: {stats.get('tp2_rate') or 0}%
🎯 TP3 rate: {stats.get('tp3_rate') or 0}%
🛑 Stops: {stats.get('stop_hits') or 0}
🛡 Break Even: {stats.get('breakeven_count') or 0}
⚠️ Invalidated: {stats.get('invalidated_count') or 0}
⌛ Expired: {stats.get('expired_count') or 0}

📈 Average MFE: {round(stats.get('avg_mfe') or 0, 2)}%
📉 Average MAE: {round(stats.get('avg_mae') or 0, 2)}%
⚖️ Average realized result: {round(stats.get('avg_realized_r') or 0, 2)}R

━━━━━━━━━━━━━━━━━━

🕘 <b>Recent lifecycle</b>

{timeline}

━━━━━━━━━━━━━━━━━━

👁 <b>Recent observations</b>

{obs_text}

━━━━━━━━━━━━━━━━━━

🧩 <b>Alternative candidates</b>

{candidate_text}
""", parse_mode="HTML")


@router.message(Command("trade"))
async def trade_replay_handler(message: Message):
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Использование: <code>/trade 12</code> или <code>/trade 12 stop</code>", parse_mode="HTML")
        return
    signal_id = int(parts[1])
    action = parts[2].lower() if len(parts) >= 3 else ""
    if action in {"stop", "close", "cancel", "стоп", "закрыть"}:
        closed = history.manual_stop(signal_id, message.from_user.id)
        if not closed:
            await message.answer("Сделка не найдена.")
            return
        await message.answer(
            f"🛑 <b>MANUAL STOP</b> · #{signal_id}\n"
            f"Цена закрытия: <code>{fmt_price(closed.get('exit_price'))}</code>\n"
            f"Результат: <b>{float(closed.get('realized_r') or 0):+.2f}R</b>",
            parse_mode="HTML",
        )
        return
    if action:
        await message.answer("Доступные действия: <code>stop</code>, <code>close</code>, <code>cancel</code>.", parse_mode="HTML")
        return
    signal = history.get_by_id(signal_id)
    if not signal or int(signal.get("owner_telegram_id") or 0) != message.from_user.id:
        await message.answer("Сделка не найдена.")
        return

    events = history.get_events(signal_id)
    event_lines = []
    meaningful_types = {
        "CREATED", "PLAN_UPDATED", "DIRECTION_FLIPPED", "DUPLICATE_RECONCILED",
        "TRIGGERED", "ACTIVE", "TP1", "BREAK_EVEN_SET", "TP2", "TP3",
        "STOP", "BREAKEVEN", "INVALIDATED", "EXPIRED", "MANUAL_STOP", "INTELLIGENCE_ALERT",
    }
    meaningful_events = [e for e in events if str(e.get("event_type")) in meaningful_types]
    labels = {
        "CREATED": "Observation promoted to trade plan",
        "PLAN_UPDATED": "Pending plan updated",
        "DIRECTION_FLIPPED": "Direction replaced before activation",
        "DUPLICATE_RECONCILED": "Legacy duplicate closed",
        "TRIGGERED": "Price entered preferred zone",
        "ACTIVE": "Trade activated",
        "TP1": "TP1 reached",
        "BREAK_EVEN_SET": "Stop moved to Break Even",
        "TP2": "TP2 reached",
        "TP3": "TP3 reached — completed",
        "STOP": "Stop Loss reached",
        "BREAKEVEN": "Closed at Break Even",
        "INVALIDATED": "Scenario invalidated",
        "EXPIRED": "Scenario expired",
        "MANUAL_STOP": "Closed manually",
        "INTELLIGENCE_ALERT": "Intelligence changed",
    }
    previous_dt = None
    for event in meaningful_events:
        created = str(event.get("created_at") or "")
        try:
            dt = datetime.fromisoformat(created)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            stamp = dt.strftime("%d.%m %H:%M")
        except ValueError:
            dt = None
            stamp = created[:16]
        elapsed = ""
        if dt is not None and previous_dt is not None:
            delta = max(0, int((dt - previous_dt).total_seconds()))
            hours, rem = divmod(delta, 3600)
            minutes = rem // 60
            elapsed = f" · +{hours}h {minutes}m" if hours else f" · +{minutes}m"
        if dt is not None:
            previous_dt = dt

        details = _event_details(event.get("details_json"))
        suffix = ""
        if event["event_type"] == "BREAK_EVEN_SET":
            suffix = " · stop → entry"
        elif event["event_type"] == "PLAN_UPDATED":
            old_plan, new_plan = details.get("old", {}), details.get("new", {})
            suffix = f" · entry {fmt_price(old_plan.get('entry'))} → {fmt_price(new_plan.get('entry'))}"
        elif event["event_type"] in {"DIRECTION_FLIPPED", "INVALIDATED"} and details.get("reason"):
            suffix = f" · {html.escape(str(details['reason']))}"
        elif event["event_type"] == "DUPLICATE_RECONCILED":
            suffix = f" · kept #{details.get('kept_signal_id', '—')}"
        elif event["event_type"] == "INTELLIGENCE_ALERT":
            suffix = f" · {details.get('health', '—')} · confidence {float(details.get('confidence') or 0):.0f}%"

        event_name = labels.get(str(event["event_type"]), str(event["event_type"]))
        event_lines.append(
            f"{_status_icon(str(event['event_type']))} <b>{html.escape(event_name)}</b>\n"
            f"   {stamp}{elapsed} · <code>{fmt_price(event.get('price')) if event.get('price') is not None else '—'}</code>{suffix}"
        )
    replay = "\n".join(event_lines) if event_lines else "Событий пока нет."
    effective_stop = signal.get("effective_stop") or signal.get("stop")
    await message.answer(
        f"""
🎞 <b>Trade Replay #{signal_id}</b>

<b>{html.escape(str(signal['symbol']))} {html.escape(str(signal['side']))}</b> · {html.escape(str(signal['timeframe']).upper())}
Status: <b>{html.escape(str(signal['status']))}</b>
Entry: <code>{fmt_price(signal['entry'])}</code>
Current / Exit: <code>{fmt_price(signal.get('exit_price') or signal.get('current_price') or signal['entry'])}</code>
Initial Stop: <code>{fmt_price(signal['stop'])}</code>
Effective Stop: <code>{fmt_price(effective_stop)}</code>
Targets: <code>{fmt_price(signal['tp1'])}</code> / <code>{fmt_price(signal['tp2'])}</code> / <code>{fmt_price(signal['tp3'])}</code>

Duration: <b>{_duration(signal.get('activated_at') or signal.get('created_at'), signal.get('closed_at'))}</b>
Pre-activation MFE / MAE: <b>{float(signal.get('pre_activation_max_profit_pct') or 0):+.2f}%</b> / <b>{float(signal.get('pre_activation_max_drawdown_pct') or 0):+.2f}%</b>
Trade MFE / MAE: <b>{float(signal.get('max_profit_pct') or 0):+.2f}%</b> / <b>{float(signal.get('max_drawdown_pct') or 0):+.2f}%</b>
Realized: <b>{float(signal.get('realized_r') or 0):+.2f}R</b>
Result: <b>{html.escape(str(signal.get('result') or 'OPEN'))}</b>
Trade Health: <b>{html.escape(str(signal.get('trade_health') or '—'))}</b>
Dynamic Confidence: <b>{float(signal.get('dynamic_confidence') or signal.get('confidence') or 0):.0f}%</b>

━━━━━━━━━━━━━━━━━━

{replay}
""",
        parse_mode="HTML",
    )
