from __future__ import annotations

from html import escape
from typing import Any


class SimilarityReport:
    def build(self, symbol: str, analysis: dict[str, Any]) -> str:
        v2 = analysis.get("similarity_v2") or {}
        if int(v2.get("samples") or 0):
            lines = [f"🔍 <b>Trade DNA Similarity — {escape(symbol.upper())}</b>", "",
                     f"Comparable trades: <b>{v2.get('samples')}</b>",
                     f"Average similarity: <b>{v2.get('average_similarity', 0)}%</b>",
                     f"Expected result: <b>{float(v2.get('expected_r', 0)):+.2f}R</b>",
                     f"Average result: <b>{float(v2.get('average_result_r', 0)):+.2f}R</b>",
                     f"Win rate: <b>{v2.get('win_rate', 0)}%</b>",
                     f"Reliability: <b>{escape(str(v2.get('reliability', 'Insufficient')))}</b>", "", "━━━━━━━━━━━━━━━━━━"]
            for title, case in (("🏆 Best match", v2.get("best_trade")), ("🛑 Worst match", v2.get("worst_trade"))):
                if not case: continue
                lines += ["", f"<b>{title}</b>",
                          f"#{case.get('signal_id')} {escape(str(case.get('symbol')))} {escape(str(case.get('side')))} · {case.get('similarity')}% · {float(case.get('realized_r',0)):+.2f}R"]
                matches = case.get("matching_reasons") or []
                differences = case.get("difference_reasons") or []
                if matches: lines.append("Similar: " + escape("; ".join(matches[:3])))
                if differences: lines.append("Different: " + escape("; ".join(differences[:2])))
            lines += ["", "Historical similarity is evidence, not a guarantee."]
            return "\n".join(lines)
        cases = analysis.get("similar_cases") or []
        stats = analysis.get("similar_stats") or {}
        if not cases:
            return f"🔍 <b>Trade DNA Similarity — {escape(symbol.upper())}</b>\n\nNo completed comparable trades yet."
        return f"🔍 <b>Trade DNA Similarity — {escape(symbol.upper())}</b>\n\nComparable trades: <b>{stats.get('samples',0)}</b>\nAverage similarity: <b>{stats.get('avg_similarity',0)}%</b>"
