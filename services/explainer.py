from __future__ import annotations

from html import escape
from typing import Any

from utils.price import fmt_number, fmt_price


class Explainer:
    """Explain deterministic market direction and execution quality separately."""

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

    @staticmethod
    def _component_text(items: list[dict[str, Any]], empty: str) -> str:
        if not items:
            return f"• {empty}"
        return "\n".join(
            f"• {escape(str(item.get('label', 'Factor')))}: {float(item.get('value', 0)):+.1f}"
            for item in items
        )

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

        if abs(edge) < 6:
            edge_explanation = "The market is effectively two-sided; neither direction has a reliable advantage."
        elif abs(edge) < 15:
            edge_explanation = "The primary direction has a modest advantage and still needs confirmation."
        elif abs(edge) < 30:
            edge_explanation = "The primary direction has a meaningful contextual advantage."
        else:
            edge_explanation = "The primary direction has a strong contextual advantage over the alternative."

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


        dna_similarity = data.get("similarity_v2") or {}
        dna_text = "Knowledge base is still collecting comparable completed trades."
        if int(dna_similarity.get("samples") or 0):
            dna_text = (f"{int(dna_similarity.get('samples') or 0)} comparable DNA patterns · "
                        f"{float(dna_similarity.get('average_similarity') or 0):.1f}% average similarity · "
                        f"expected {float(dna_similarity.get('expected_r') or 0):+.2f}R · "
                        f"{dna_similarity.get('reliability', 'Insufficient')} reliability")

        breakdown = data.get("direction_breakdown", {})
        breakdown_text = "\n".join(
            f"• {escape(str(name))}: {float(value):+.1f}"
            for name, value in breakdown.items()
        ) or "• No breakdown available"
        drivers = self._component_text(data.get("strongest_drivers", []), "No major positive driver")
        negative = self._component_text(data.get("biggest_blockers", []), "No major negative component")

        return f"""
🧠 <b>Explain Pro — {escape(symbol.upper())}</b>

━━━━━━━━━━━━━━━━━━

🧭 <b>Market direction</b>
{escape(str(data.get('market_bias', 'Unknown')))}
Direction score: {fmt_number(data.get('direction_score', 0), 1)}/100

⚡ <b>Execution bias</b>
{escape(str(data.get('execution_bias', 'NEUTRAL / OBSERVE')))}
{escape(str(data.get('execution_status', 'WATCHLIST')))}

{escape(edge_explanation)}

━━━━━━━━━━━━━━━━━━

🧮 <b>Why the direction score looks this way</b>
{breakdown_text}

🚀 <b>Strongest drivers</b>
{drivers}

🚧 <b>Biggest blockers</b>
{negative}

━━━━━━━━━━━━━━━━━━

📊 <b>Execution quality</b>
Entry: {fmt_number(data.get('entry_quality', 0), 1)}/100 — {self._grade(float(data.get('entry_quality', 0)))}
Risk: {fmt_number(data.get('risk_quality', 0), 1)}/100 — {self._grade(float(data.get('risk_quality', 0)))}
Readiness: {fmt_number(data.get('execution_readiness', 0), 1)}/100 — {self._grade(float(data.get('execution_readiness', 0)))}
AI Grade: {escape(str(data.get('ai_grade', 'N/A')))}

<b>Final verdict:</b>
{escape(str(data.get('final_verdict', 'Observe current conditions.')))}

━━━━━━━━━━━━━━━━━━

✅ <b>What supports {escape(direction)}</b>
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

🧬 <b>Trade DNA evidence</b>
{escape(dna_text)}
""".strip()
