from __future__ import annotations

from html import escape
from typing import Any

from utils.price import fmt_number, fmt_price


class Explainer:
    """Turns a structured analysis into an explainable trading report.

    This engine does not invent probabilities. It only explains values that are
    already present in the deterministic analysis result.
    """

    @staticmethod
    def _clean_reason(reason: str) -> str:
        return reason.replace("✅ ", "").replace("⚠️ ", "").replace("⛔ ", "").strip()

    @staticmethod
    def _grade(value: float) -> str:
        if value >= 75:
            return "Strong"
        if value >= 58:
            return "Moderate"
        if value >= 42:
            return "Weak"
        return "Very weak"

    def build(self, data: dict[str, Any], symbol: str) -> str:
        direction = str(data.get("direction", "NEUTRAL"))
        opposite = "SHORT" if direction == "LONG" else "LONG"
        positives = [x for x in data.get("reasons", []) if str(x).startswith("✅")]
        warnings = [x for x in data.get("reasons", []) if str(x).startswith("⚠️")]
        blockers = [x for x in data.get("reasons", []) if str(x).startswith("⛔")]
        triggers = data.get("triggers", []) or ["No additional trigger is currently required"]
        alternatives = data.get("alternative_conditions", []) or []

        positive_text = "\n".join(f"• {escape(self._clean_reason(x))}" for x in positives) or "• No strong directional confirmation"
        warning_text = "\n".join(f"• {escape(self._clean_reason(x))}" for x in warnings) or "• No secondary warning"
        blocker_text = "\n".join(f"• {escape(self._clean_reason(x))}" for x in blockers) or "• No hard execution blocker"
        trigger_text = "\n".join(f"• {escape(str(x))}" for x in triggers)
        alternative_text = "\n".join(f"• {escape(str(x))}" for x in alternatives) or "• Opposite structure confirmation"

        location = str(data.get("premium", {}).get("zone", "Unknown"))
        position = float(data.get("premium", {}).get("premium", 50.0))
        edge = float(data.get("directional_edge", 0.0))
        current_price = float(data.get("price", 0.0))
        preferred_low = float(data.get("preferred_entry_low", current_price))
        preferred_high = float(data.get("preferred_entry_high", current_price))
        stop = float(data.get("stop", 0.0))

        if abs(edge) < 5:
            edge_explanation = "The directional advantage is small, so the market is effectively two-sided."
        elif abs(edge) < 15:
            edge_explanation = "The primary scenario has only a modest advantage and needs confirmation."
        elif abs(edge) < 30:
            edge_explanation = "The primary scenario has a meaningful directional advantage."
        else:
            edge_explanation = "The primary scenario has a strong directional advantage over the alternative."

        exact = data.get("historical_probability") or {}
        similar = data.get("similar_stats") or {}
        if int(exact.get("samples") or 0) >= 5:
            historical_text = (
                f"Exact completed samples: {int(exact.get('samples') or 0)}\n"
                f"TP1 {exact.get('tp1_rate', 0)}% · TP2 {exact.get('tp2_rate', 0)}% · "
                f"TP3 {exact.get('tp3_rate', 0)}% · Stop {exact.get('stop_rate', 0)}%\n"
                f"Reliability: {exact.get('reliability', 'Insufficient')}"
            )
        elif int(similar.get("samples") or 0) > 0:
            historical_text = (
                f"Similar completed setups: {int(similar.get('samples') or 0)}\n"
                f"TP1 {similar.get('tp1_rate', 0)}% · TP2 {similar.get('tp2_rate', 0)}% · "
                f"TP3 {similar.get('tp3_rate', 0)}% · Stop {similar.get('stop_rate', 0)}%\n"
                f"Reliability: {similar.get('reliability', 'Insufficient')}"
            )
        else:
            historical_text = "No completed historical sample yet. The system is collecting outcomes."

        if data.get("execution_status") == "🟢 READY":
            execution_view = "The setup currently passes the execution filters, but risk management remains mandatory."
        elif data.get("execution_status") == "🎯 WAIT FOR PULLBACK":
            execution_view = "Direction may be valid, but the current entry is inefficient. The preferred plan is to wait for price to return to the calculated zone."
        elif data.get("execution_status") == "🟡 WAIT FOR TRIGGER":
            execution_view = "The directional idea exists, but structure or momentum has not confirmed execution yet."
        else:
            execution_view = "This is an observation scenario rather than an executable trade."

        return f"""
🧠 <b>Explain Pro — {escape(symbol.upper())}</b>

━━━━━━━━━━━━━━━━━━

🎯 <b>Why {escape(direction)}?</b>
{positive_text}

⚖️ <b>Directional edge</b>
LONG {fmt_number(data.get('long_score', 0), 1)} / SHORT {fmt_number(data.get('short_score', 0), 1)}
Edge: {float(data.get('directional_edge', 0)):+.1f}

{escape(edge_explanation)}

━━━━━━━━━━━━━━━━━━

📊 <b>Score interpretation</b>
Direction: {fmt_number(data.get('direction_score', 0), 1)}/100 — {self._grade(float(data.get('direction_score', 0)))}
Entry: {fmt_number(data.get('entry_quality', 0), 1)}/100 — {self._grade(float(data.get('entry_quality', 0)))}
Risk: {fmt_number(data.get('risk_quality', 0), 1)}/100 — {self._grade(float(data.get('risk_quality', 0)))}
Readiness: {fmt_number(data.get('execution_readiness', 0), 1)}/100 — {self._grade(float(data.get('execution_readiness', 0)))}

{escape(execution_view)}

━━━━━━━━━━━━━━━━━━

✅ <b>What supports the setup</b>
{positive_text}

⚠️ <b>What weakens it</b>
{warning_text}

⛔ <b>What blocks execution</b>
{blocker_text}

━━━━━━━━━━━━━━━━━━

📍 <b>Entry logic</b>
Current price: {fmt_price(current_price)}
Location: {escape(location)} ({fmt_number(position, 2)}%)
Preferred zone: {fmt_price(preferred_low)} – {fmt_price(preferred_high)}
Invalidation / stop reference: {fmt_price(stop)}

🔔 <b>What must happen next</b>
{trigger_text}

🔄 <b>What would strengthen {escape(opposite)}</b>
{alternative_text}

━━━━━━━━━━━━━━━━━━

📚 <b>Historical intelligence</b>
{escape(historical_text)}

━━━━━━━━━━━━━━━━━━

🧩 <b>Analyst conclusion</b>
The system currently prefers <b>{escape(direction)}</b>, but the recommendation is <b>{escape(str(data.get('recommendation', 'OBSERVE')))}</b>. The most important distinction is between directional quality and entry quality: a correct market bias does not automatically mean the current price is a good entry.
""".strip()
