from __future__ import annotations

from typing import Any

from utils.price import fmt_price


class ExplainPro:
    """Human-readable reasoning layer built from deterministic analysis data."""

    @staticmethod
    def _clean(items: list[str], prefix: str) -> list[str]:
        return [item.replace(prefix, "", 1).strip() for item in items if item.startswith(prefix)]

    def build(self, data: dict[str, Any]) -> str:
        direction = data["direction"]
        opposite = "SHORT" if direction == "LONG" else "LONG"
        positives = self._clean(data.get("reasons", []), "✅")
        warnings = self._clean(data.get("reasons", []), "⚠️")
        blockers = self._clean(data.get("reasons", []), "⛔")
        triggers = data.get("triggers", [])

        why = "\n".join(f"• {item}" for item in positives[:7]) or "• Нет достаточного набора независимых подтверждений."
        risks = "\n".join(f"• {item}" for item in (blockers + warnings)[:7]) or "• Критических конфликтов не обнаружено."
        trigger_text = "\n".join(f"• {item}" for item in triggers[:5]) or "• Текущие условия уже соответствуют сценарию."

        invalidation: list[str] = []
        if direction == "LONG":
            invalidation.extend([
                f"• Закрепление ниже Stop: {fmt_price(data['stop'])}",
                "• Bearish BOS/CHOCH против основного сценария",
                "• Потеря bullish-зоны с импульсным объёмом",
            ])
        else:
            invalidation.extend([
                f"• Закрепление выше Stop: {fmt_price(data['stop'])}",
                "• Bullish BOS/CHOCH против основного сценария",
                "• Потеря bearish-зоны с импульсным объёмом",
            ])

        location = data["premium"]["zone"].split(" ", 1)[-1]
        preferred = f"{fmt_price(data['preferred_entry_low'])} – {fmt_price(data['preferred_entry_high'])}"
        probability = data.get("historical_probability") or {}
        if probability.get("sample_size", 0) >= 30:
            history = (
                f"Найдено похожих завершённых сетапов: <b>{probability['sample_size']}</b>\n"
                f"TP1: <b>{probability['tp1_rate']}%</b> | TP2: <b>{probability['tp2_rate']}%</b> | "
                f"TP3: <b>{probability['tp3_rate']}%</b> | Stop: <b>{probability['stop_rate']}%</b>"
            )
        else:
            history = (
                f"Выборка пока мала: <b>{probability.get('sample_size', 0)}</b> завершённых похожих сетапов.\n"
                "Историческая вероятность появится после накопления минимум 30 наблюдений."
            )

        return f"""
🧠 <b>Explain Pro</b>

🎯 <b>Почему основной сценарий — {direction}</b>
{why}

⚠️ <b>Что ослабляет сценарий</b>
{risks}

📍 <b>Почему не обязательно входить прямо сейчас</b>
Цена находится в зоне <b>{location}</b>. Предпочтительная область исполнения: <b>{preferred}</b>.
Статус исполнения: <b>{data['execution_status']}</b>.

🔔 <b>Что должно произойти дальше</b>
{trigger_text}

❌ <b>Что отменит сценарий</b>
{chr(10).join(invalidation)}

🔁 <b>Когда усилится альтернативный {opposite}</b>
{chr(10).join('• ' + item for item in data.get('alternative_conditions', [])[:4])}

📊 <b>Историческая база</b>
{history}

<i>Это объяснение основано на текущих рыночных признаках и накопленной статистике, а не является финансовой гарантией.</i>
"""
