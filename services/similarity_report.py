from __future__ import annotations

from html import escape
from typing import Any


class SimilarityReport:
    @staticmethod
    def build(symbol: str, analysis: dict[str, Any]) -> str:
        stats = analysis.get("similar_stats") or {}
        cases = analysis.get("similar_cases") or []
        samples = int(stats.get("samples") or 0)
        if not samples:
            return (
                f"🧩 <b>Similar Setups — {escape(symbol)}</b>\n\n"
                "Пока нет завершённых исторических сетапов, достаточно похожих на текущий. "
                "Система начнёт показывать статистику после накопления результатов."
            )
        lines = [
            f"🧩 <b>Similar Setups — {escape(symbol)}</b>",
            "",
            f"Found: <b>{samples}</b>",
            f"Average similarity: <b>{stats.get('avg_similarity', 0)}%</b>",
            f"Reliability: <b>{escape(str(stats.get('reliability', 'Insufficient')))}</b>",
            "",
            f"🎯 TP1: <b>{stats.get('tp1_rate', 0)}%</b>",
            f"🎯 TP2: <b>{stats.get('tp2_rate', 0)}%</b>",
            f"🏆 TP3: <b>{stats.get('tp3_rate', 0)}%</b>",
            f"🛑 Stop: <b>{stats.get('stop_rate', 0)}%</b>",
            f"📈 Avg MFE: <b>{stats.get('avg_mfe', 0)}%</b>",
            f"📉 Avg MAE: <b>{stats.get('avg_mae', 0)}%</b>",
            "",
            "━━━━━━━━━━━━━━━━━━",
            "",
            "<b>Closest historical cases</b>",
        ]
        for case in cases[:5]:
            outcome = "TP3" if case.get("tp3_hit") else "TP2" if case.get("tp2_hit") else "TP1" if case.get("tp1_hit") else "STOP" if case.get("stop_hit") else case.get("status")
            lines.append(
                f"• #{case.get('signal_id')} {escape(str(case.get('symbol')))} {escape(str(case.get('side')))} — "
                f"{case.get('similarity')}% · <b>{escape(str(outcome))}</b>"
            )
        lines.extend(["", "Percentages are historical observations, not guarantees."])
        return "\n".join(lines)
