from __future__ import annotations

from typing import Any

from utils.price import fmt_price


class ExplainPro:
    """Deterministic analyst-style reasoning without pretending to be a predictive LLM."""

    @staticmethod
    def _clean(items: list[str], prefix: str) -> list[str]:
        return [item.replace(prefix, "", 1).strip() for item in items if item.startswith(prefix)]

    @staticmethod
    def _priority(item: str) -> int:
        order = {
            "trend": 1, "structure": 2, "bos": 3, "choch": 3, "liquidity": 4,
            "order block": 5, "breaker": 5, "mitigation": 5, "fvg": 6,
            "displacement": 7, "volume": 8, "momentum": 9, "rsi": 10,
        }
        lowered = item.lower()
        return min((rank for key, rank in order.items() if key in lowered), default=99)

    def build(self, data: dict[str, Any]) -> str:
        direction = data["direction"]
        opposite = "SHORT" if direction == "LONG" else "LONG"
        reasons = data.get("reasons", [])
        positives = sorted(self._clean(reasons, "✅"), key=self._priority)
        warnings = sorted(self._clean(reasons, "⚠️"), key=self._priority)
        blockers = sorted(self._clean(reasons, "⛔"), key=self._priority)
        triggers = data.get("triggers", [])

        why = "\n".join(f"• {item}" for item in positives[:8]) or "• Независимых подтверждений пока недостаточно."
        risks = "\n".join(f"• {item}" for item in (blockers + warnings)[:8]) or "• Критических конфликтов не обнаружено."
        trigger_text = "\n".join(f"• {item}" for item in triggers[:6]) or "• Условия исполнения уже сформированы."

        invalidation = [
            f"• Закрепление {'ниже' if direction == 'LONG' else 'выше'} Stop: {fmt_price(data['stop'])}",
            f"• {'Bearish' if direction == 'LONG' else 'Bullish'} BOS/CHOCH против основного сценария",
            f"• Сильный {opposite.lower()} displacement с расширением объёма",
        ]
        location = data["premium"]["zone"].split(" ", 1)[-1]
        preferred = f"{fmt_price(data['preferred_entry_low'])} – {fmt_price(data['preferred_entry_high'])}"
        probability = data.get("historical_probability") or {}
        samples = int(probability.get("sample_size", 0) or 0)
        if samples >= 30:
            history = (
                f"Завершённых похожих сетапов: <b>{samples}</b>\n"
                f"TP1 <b>{probability['tp1_rate']}%</b> · TP2 <b>{probability['tp2_rate']}%</b> · "
                f"TP3 <b>{probability['tp3_rate']}%</b> · Stop <b>{probability['stop_rate']}%</b>"
            )
        else:
            history = f"Выборка: <b>{samples}/30</b>. До порога надёжности статистика не используется как вероятность."

        edge = float(data.get("directional_edge", 0))
        conviction = "выраженное" if abs(edge) >= 25 else "умеренное" if abs(edge) >= 10 else "слабое"
        execution_note = (
            "Направление сильнее качества текущего входа: сценарий следует наблюдать, но не догонять цену."
            if float(data.get("direction_score", 0)) - float(data.get("entry_quality", 0)) >= 15
            else "Качество направления и входа относительно сбалансированы."
        )

        return f"""
🧠 <b>Explain Pro 2.0</b>

🧭 <b>Analyst Verdict</b>
Преимущество {direction}: <b>{conviction}</b> (edge {edge:+.1f}).
{execution_note}

🎯 <b>Почему основной сценарий — {direction}</b>
{why}

⚠️ <b>Что снижает качество</b>
{risks}

📍 <b>План исполнения</b>
Текущая локация: <b>{location}</b>.
Предпочтительная зона: <b>{preferred}</b>.
Execution: <b>{data['execution_status']}</b> · Readiness: <b>{data.get('execution_readiness', 0)}/100</b>.

🔔 <b>Что должно произойти дальше</b>
{trigger_text}

❌ <b>Что отменит сценарий</b>
{chr(10).join(invalidation)}

🔁 <b>Когда усилится {opposite}</b>
{chr(10).join('• ' + item for item in data.get('alternative_conditions', [])[:5])}

📊 <b>Historical Evidence</b>
{history}

<i>Разбор формируется из прозрачных правил и сохранённой статистики. Он не является гарантией результата.</i>
"""
