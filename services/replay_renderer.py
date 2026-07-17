from __future__ import annotations

import html
from typing import Any

from utils.price import fmt_price


def _v(value: Any, default: str = "—") -> str:
    text = str(value if value not in (None, "", "UNKNOWN") else default)
    return html.escape(text)


def _score(value: Any) -> str:
    try:
        return f"{float(value):.0f}/100"
    except (TypeError, ValueError):
        return "—"


def _lines(values: list[str] | None) -> str:
    return "\n".join(f"• {html.escape(str(x))}" for x in (values or [])) or "• —"


def render_intelligence(signal: dict[str, Any], intelligence: dict[str, Any]) -> list[str]:
    dna = intelligence.get("dna") or {}
    memory = intelligence.get("memory") or {}
    similarity = intelligence.get("similarity") or {}
    historical = intelligence.get("historical") or {}
    cases = intelligence.get("similar_trades") or []

    setup_grade = dna.get("ai_grade") or "N/A"
    execution_rating = round((float(dna.get("entry_quality") or 0) + float(dna.get("risk_quality") or 0) + float(dna.get("readiness") or 0)) / 3)
    dna_card = f"""🧬 <b>Trade DNA · {_v(dna.get('fingerprint'))}</b>

Regime: <b>{_v(dna.get('market_regime'))}</b> · Trend: <b>{_v(dna.get('trend'))}</b>
Structure: {_v(dna.get('structure'))}
BOS / CHOCH: {_v(dna.get('bos'))} / {_v(dna.get('choch'))}
Liquidity: {_v(dna.get('liquidity'))}
Liquidity Event: {_v(dna.get('liquidity_event'))}
OB / Breaker / Mitigation: {_v(dna.get('order_block'))} / {_v(dna.get('breaker'))} / {_v(dna.get('mitigation'))}
FVG: {_v(dna.get('fvg'))} · Zone: {_v(dna.get('premium_discount'))}
Volume: {_v(dna.get('volume'))} · Session: {_v(dna.get('session'))}
ATR: {float(dna.get('atr_pct') or 0):.2f}% · RSI: {float(dna.get('rsi') or 0):.1f}
EMA50 / EMA200 distance: {float(dna.get('ema50_distance_pct') or 0):+.2f}% / {float(dna.get('ema200_distance_pct') or 0):+.2f}%
MACD: {_v(dna.get('macd'))}

Entry Quality: <b>{_score(dna.get('entry_quality'))}</b>
Risk Quality: <b>{_score(dna.get('risk_quality'))}</b>
Trade Health: <b>{_score(dna.get('trade_health'))}</b>
Execution Rating: <b>{execution_rating}/100</b>
Setup Grade / AI Grade: <b>{_v(setup_grade)}</b>"""

    best = similarity.get("best_match") or {}
    worst = similarity.get("worst_match") or {}
    case_lines = []
    for case in cases[:3]:
        case_lines.append(f"• #{case.get('signal_id')} {case.get('symbol')} · {float(case.get('similarity') or 0):.1f}% · {float(case.get('realized_r') or 0):+.2f}R")
    similar_card = f"""🔍 <b>Similar Trades 2.0</b>

Samples: <b>{int(similarity.get('samples') or 0)}</b> · Reliability: <b>{_v(similarity.get('reliability'))}</b>
Expected R: <b>{float(similarity.get('expected_r') or 0):+.2f}R</b>
Average similarity: <b>{float(similarity.get('average_similarity') or 0):.1f}%</b>
Best Match: <b>{float(best.get('similarity') or 0):.1f}%</b> · #{best.get('signal_id', '—')}
Worst Match: <b>{float(worst.get('similarity') or 0):.1f}%</b> · #{worst.get('signal_id', '—')}

{chr(10).join(case_lines) or '• Comparable trades are still being collected.'}"""

    history_card = f"""📊 <b>Historical Intelligence</b>

Completed analogues: <b>{int(historical.get('samples') or 0)}</b>
Win rate: <b>{float(historical.get('win_rate') or 0):.1f}%</b>
Expected result: <b>{float(historical.get('expected_r') or 0):+.2f}R</b>
Average MFE / MAE: <b>{float(historical.get('avg_mfe') or 0):+.2f}%</b> / <b>{float(historical.get('avg_mae') or 0):+.2f}%</b>
Reliability: <b>{_v(historical.get('reliability'))}</b>"""

    memory_card = f"""🧠 <b>AI Memory</b>

<b>What worked</b>
{_lines(memory.get('what_worked'))}

<b>What failed</b>
{_lines(memory.get('what_failed'))}

<b>Strengths</b>
{_lines(memory.get('strengths'))}

<b>Weaknesses</b>
{_lines(memory.get('weaknesses'))}

<b>Lesson learned</b>
{html.escape(str(memory.get('lesson') or 'Memory will be created after the trade closes.'))}"""
    return [dna_card, similar_card + "\n\n" + history_card, memory_card]
