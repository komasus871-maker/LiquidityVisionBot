from __future__ import annotations

import html
import json
import logging
from datetime import datetime, timezone

from aiogram import Bot

from database.signal_history import SignalHistory

from services.probability_engine import ProbabilityEngine
from utils.price import fmt_price


class Notifier:
    def __init__(self, bot: Bot | None = None):
        self.bot = bot
        self.probability = ProbabilityEngine()
        self.history = SignalHistory()

    @staticmethod
    def _duration(started_at: str | None) -> str | None:
        if not started_at:
            return None
        try:
            start = datetime.fromisoformat(started_at)
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            seconds = max(0, int((datetime.now(timezone.utc) - start).total_seconds()))
        except (TypeError, ValueError):
            return None
        hours, remainder = divmod(seconds, 3600)
        minutes = remainder // 60
        if hours:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"

    @staticmethod
    def _progress(signal: dict, price: float) -> float:
        entry = float(signal.get("entry") or 0)
        if not entry:
            return 0.0
        move = (price - entry) / entry * 100
        return round(move if signal.get("side") == "LONG" else -move, 2)

    @staticmethod
    def _target_progress(side: str, entry: float, target: float, price: float) -> float:
        distance = abs(target - entry)
        if distance <= 0:
            return 0.0
        move = (price - entry) if side == "LONG" else (entry - price)
        return max(0.0, min(100.0, move / distance * 100))

    @staticmethod
    def _risk_remaining(side: str, entry: float, stop: float, price: float) -> float:
        distance = abs(entry - stop)
        if distance <= 0:
            return 0.0
        remaining = (price - stop) if side == "LONG" else (stop - price)
        return max(0.0, min(100.0, remaining / distance * 100))

    @staticmethod
    def _bar(value: float, width: int = 10) -> str:
        value = max(0.0, min(100.0, value))
        filled = round(value / 100 * width)
        return "█" * filled + "░" * (width - filled)

    @staticmethod
    def _r_multiple(signal: dict, price: float) -> float:
        entry = float(signal.get("entry") or 0)
        stop = float(signal.get("stop") or 0)
        risk = abs(entry - stop)
        if not entry or risk <= 0:
            return 0.0
        move = (price - entry) if signal.get("side") == "LONG" else (entry - price)
        return move / risk

    async def _send(self, signal: dict, lines: list[str]) -> None:
        if not self.bot:
            return
        chat_id = signal.get("notification_chat_id") or signal.get("owner_telegram_id")
        if not chat_id:
            return
        try:
            await self.bot.send_message(chat_id, "\n".join(lines), parse_mode="HTML")
        except Exception:
            logging.exception("Failed to send notification for signal %s", signal.get("id"))

    async def lifecycle(self, signal: dict, event: str, price: float, extra: str = "") -> None:
        icons = {
            "TRIGGERED": "🔔", "ACTIVE": "🟢", "TP1": "🎯", "TP2": "🏆", "TP3": "👑",
            "STOP": "🛑", "BREAKEVEN": "🛡", "INVALIDATED": "⚠️", "EXPIRED": "⌛",
        }
        titles = {
            "TRIGGERED": "Цена вошла в preferred entry zone",
            "ACTIVE": "Сетап активирован",
            "TP1": "TP1 достигнут",
            "TP2": "TP2 достигнут",
            "TP3": "TP3 достигнут — сделка завершена",
            "STOP": "Stop Loss достигнут",
            "BREAKEVEN": "Сделка закрыта в безубыток",
            "INVALIDATED": "Сетап инвалидирован до активации",
            "EXPIRED": "Сетап истёк без активации",
        }

        move_pct = self._progress(signal, price)
        r_value = self._r_multiple(signal, price)
        duration = self._duration(signal.get("activated_at") or signal.get("created_at"))
        stats = self.probability.exact_stats(
            str(signal.get("setup_key") or ""),
            str(signal.get("timeframe") or "1h"),
            str(signal.get("side") or "LONG"),
        )

        lines = [
            f"{icons.get(event, '🔔')} <b>{html.escape(str(signal['symbol']))} {html.escape(str(signal['side']))}</b>",
            "",
            f"<b>{titles.get(event, event)}</b>",
            f"Signal ID: <code>{signal['id']}</code>",
            f"Price: <code>{fmt_price(price)}</code>",
            f"Status: <b>{event}</b>",
        ]
        if event in {"ACTIVE", "TP1", "TP2", "TP3", "STOP", "BREAKEVEN"}:
            lines.extend([
                f"Current move: <b>{move_pct:+.2f}%</b>",
                f"Current R: <b>{r_value:+.2f}R</b>",
            ])
        if duration:
            lines.append(f"Duration: <b>{duration}</b>")

        if event == "TRIGGERED":
            lines.append("Next: waiting for a directional reaction candle.")
        elif event == "ACTIVE":
            lines.extend([
                "",
                f"Entry: <code>{fmt_price(float(signal['entry']))}</code>",
                f"Stop: <code>{fmt_price(float(signal.get('effective_stop') or signal['stop']))}</code>",
                f"Targets: {fmt_price(float(signal['tp1']))} / {fmt_price(float(signal['tp2']))} / {fmt_price(float(signal['tp3']))}",
            ])
        elif event == "TP1":
            lines.append(f"Next target: <b>{fmt_price(float(signal['tp2']))}</b>")
            if signal.get("break_even_at"):
                lines.append("🛡 Stop automatically moved to Break Even.")
        elif event == "TP2":
            lines.append(f"Final target: <b>{fmt_price(float(signal['tp3']))}</b>")
        elif event in {"TP3", "STOP", "BREAKEVEN"}:
            lines.extend([
                f"MFE: <b>{float(signal.get('max_profit_pct') or 0):.2f}%</b>",
                f"MAE: <b>{float(signal.get('max_drawdown_pct') or 0):.2f}%</b>",
                f"Realized result: <b>{float(signal.get('realized_r') or 0):+.2f}R</b>",
            ])

        if int(stats.get("samples") or 0) >= 5:
            lines.extend([
                "",
                "📚 <b>Historical exact-setup context</b>",
                f"Samples: {stats['samples']} · TP1 {stats['tp1_rate']}% · TP2 {stats['tp2_rate']}% · Stop {stats['stop_rate']}%",
                f"Reliability: {html.escape(str(stats['reliability']))}",
            ])
        if extra:
            lines.extend(["", html.escape(extra)])
        await self._send(signal, lines)

    def _canonical_intelligence(self, signal: dict) -> dict:
        latest = self.history.get_latest_intelligence_snapshot(int(signal.get("id") or 0)) if signal.get("id") else None
        if latest:
            return latest
        try:
            return json.loads(signal.get("intelligence_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            return {}

    def _confidence_components(self, signal: dict) -> tuple[float, dict[str, float], dict]:
        intelligence = self._canonical_intelligence(signal)
        confidence = float(intelligence.get("confidence", signal.get("dynamic_confidence") or signal.get("confidence") or 0) or 0)
        groups = {
            "Trend": float(intelligence.get("trend", 50) or 50),
            "Structure": float(intelligence.get("structure", 50) or 50),
            "Liquidity": float(intelligence.get("liquidity", 50) or 50),
            "Momentum": float(intelligence.get("momentum", 50) or 50),
        }
        return max(0.0, min(100.0, confidence)), {k: max(0.0, min(100.0, v)) for k, v in groups.items()}, intelligence

    async def progress(self, signal: dict, price: float) -> None:
        entry = float(signal["entry"])
        stop = float(signal.get("effective_stop") or signal["stop"])
        side = str(signal["side"])
        tp1 = self._target_progress(side, entry, float(signal["tp1"]), price)
        tp2 = self._target_progress(side, entry, float(signal["tp2"]), price)
        tp3 = self._target_progress(side, entry, float(signal["tp3"]), price)
        distance_to_sl = self._risk_remaining(side, entry, stop, price)
        risk_used = max(0.0, min(100.0, 100.0 - distance_to_sl))
        duration = self._duration(signal.get("activated_at") or signal.get("created_at")) or "—"
        move_pct = self._progress(signal, price)
        r_value = self._r_multiple(signal, price)
        confidence, confidence_groups, intelligence = self._confidence_components(signal)
        previous_confidence = float(signal.get("previous_confidence") or confidence)
        confidence_delta = confidence - previous_confidence
        health = str(intelligence.get("health") or signal.get("trade_health") or "🟡 STABLE")
        commentary = str(intelligence.get("commentary") or "")
        probability = self.probability.live_context(signal)

        lines = [
            f"📡 <b>{html.escape(str(signal['symbol']))} {html.escape(side)} · LIVE</b>",
            "",
            f"Status: <b>{html.escape(str(signal['status']))}</b>",
            f"Entry / Current: <code>{fmt_price(entry)}</code> → <code>{fmt_price(price)}</code>",
            f"PnL: <b>{move_pct:+.2f}%</b> · <b>{r_value:+.2f}R</b>",
            f"Trade Health: <b>{float(intelligence.get('health_score', signal.get('health_score') or 0)):.0f}% · {html.escape(health)}</b>",
            "",
            f"TP1 {self._bar(tp1)} {tp1:.0f}%",
            f"TP2 {self._bar(tp2)} {tp2:.0f}%",
            f"TP3 {self._bar(tp3)} {tp3:.0f}%",
            f"Risk used {self._bar(risk_used)} {risk_used:.0f}%",
            f"Distance to SL: <b>{distance_to_sl:.0f}%</b>",
            "",
            f"🧠 Confidence {self._bar(confidence)} {confidence:.0f}% ({confidence_delta:+.0f}%)",
            f"Trend {self._bar(confidence_groups.get('Trend', 50))} {confidence_groups.get('Trend', 50):.0f}%",
            f"Structure {self._bar(confidence_groups.get('Structure', 50))} {confidence_groups.get('Structure', 50):.0f}%",
            f"Liquidity {self._bar(confidence_groups.get('Liquidity', 50))} {confidence_groups.get('Liquidity', 50):.0f}%",
            f"Momentum {self._bar(confidence_groups.get('Momentum', 50))} {confidence_groups.get('Momentum', 50):.0f}%",
            "",
            "📊 <b>Probability Engine</b>",
            (f"TP1 {probability['tp1_rate']:.0f}% · TP2 {probability['tp2_rate']:.0f}% · TP3 {probability['tp3_rate']:.0f}% · Stop {probability['stop_rate']:.0f}%"
             if probability['samples'] else "Learning: no completed historical sample yet."),
            (f"Sample: {probability['samples']} · Reliability: {html.escape(probability['reliability'])}"
             if probability['samples'] else "Historical estimates will appear after completed trades."),
            "",
            "🤖 <b>AI Commentary</b>",
            html.escape(commentary or "The trade remains under live monitoring."),
            "",
            f"Duration: <b>{duration}</b>",
            f"MFE / MAE: <b>{float(signal.get('max_profit_pct') or 0):+.2f}%</b> / <b>{float(signal.get('max_drawdown_pct') or 0):+.2f}%</b>",
        ]
        if signal.get("break_even_at"):
            lines.append("🛡 Break Even protection is active.")
        await self._send(signal, lines)

    async def smart_alert(self, signal: dict, price: float, reasons: list[str]) -> None:
        if not reasons:
            return
        confidence, _, intelligence = self._confidence_components(signal)
        health = str(intelligence.get("health") or signal.get("trade_health") or "🟡 STABLE")
        probability = self.probability.live_context(signal)
        commentary = str(intelligence.get("commentary") or "")
        lines = [
            f"🧠 <b>{html.escape(str(signal['symbol']))} {html.escape(str(signal['side']))} · INTELLIGENCE ALERT</b>",
            "",
            f"Trade Health: <b>{html.escape(health)}</b>",
            f"Dynamic Confidence: <b>{confidence:.0f}%</b>",
            f"Current price: <code>{fmt_price(price)}</code>",
            "",
            "<b>What changed</b>",
            *[f"• {html.escape(reason)}" for reason in reasons],
        ]
        if probability['samples']:
            lines.extend([
                "",
                "📊 <b>Historical probability</b>",
                f"TP1 {probability['tp1_rate']:.0f}% · TP2 {probability['tp2_rate']:.0f}% · Stop {probability['stop_rate']:.0f}%",
                f"Sample {probability['samples']} · {html.escape(probability['reliability'])}",
            ])
        if commentary:
            lines.extend(["", "🤖 <b>AI Commentary</b>", html.escape(commentary)])
        await self._send(signal, lines)

    async def break_even(self, signal: dict, price: float) -> None:
        lines = [
            f"🛡 <b>{html.escape(str(signal['symbol']))} {html.escape(str(signal['side']))}</b>",
            "",
            "<b>Position secured</b>",
            f"Signal ID: <code>{signal['id']}</code>",
            f"Stop moved to Break Even: <code>{fmt_price(price)}</code>",
            "If price returns to entry, the lifecycle will close as BREAKEVEN instead of STOP.",
        ]
        await self._send(signal, lines)
