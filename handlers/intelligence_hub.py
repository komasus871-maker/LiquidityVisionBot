from __future__ import annotations

import html

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from services.performance_intelligence import PerformanceIntelligence, Segment
from services.premium import PremiumService
from utils.price import fmt_price

router = Router()
engine = PerformanceIntelligence()
premium = PremiumService()


def _segment(x: Segment | None) -> str:
    if not x:
        return "—"
    return f"{html.escape(x.name)} · {x.trades} trades · {x.win_rate:.1f}% WR · {x.expectancy:+.2f}R EV · {x.maturity}"


@router.message(Command("performance"))
async def performance_handler(message: Message):
    r = engine.performance(message.from_user.id)
    top = "\n".join(f"• {_segment(x)}" for x in r["symbols"][:3]) or "• insufficient data"
    await message.answer(
        "📊 <b>PERFORMANCE COMMAND CENTER</b>\n\n"
        f"Resolved trades: <b>{r['trades']}</b>\n"
        f"Win Rate: <b>{r['win_rate']:.2f}%</b> · Wins/Losses: <b>{r['wins']}/{r['losses']}</b>\n"
        f"Net result: <b>{r['net_r']:+.2f}R</b>\n"
        f"Expectancy: <b>{r['expectancy']:+.2f}R</b> per trade\n"
        f"Profit Factor: <b>{r['profit_factor']:.2f}</b>\n"
        f"Average win/loss: <b>{r['avg_win']:+.2f}R / {r['avg_loss']:+.2f}R</b>\n"
        f"Average hold: <b>{r['avg_hold_hours']:.1f}h</b>\n"
        f"Current streak: <b>{r['streak']} {r['streak_type']}</b>\n\n"
        f"<b>Top markets by expectancy</b>\n{top}", parse_mode="HTML")


@router.message(Command("portfolio"))
async def portfolio_handler(message: Message):
    r = engine.portfolio(message.from_user.id)
    if not r["active"]:
        await message.answer("🧭 <b>PORTFOLIO INTELLIGENCE</b>\n\nNo active positions. Portfolio heat: <b>0R · LOW</b>", parse_mode="HTML")
        return
    positions = []
    for x in r["active"][:12]:
        positions.append(
            f"• #{x['id']} {html.escape(str(x['symbol']))} {html.escape(str(x['side']))} · {html.escape(str(x.get('timeframe') or '—'))}\n"
            f"  {fmt_price(x.get('entry'))} → {fmt_price(x.get('current_price') or x.get('entry'))} · {x.get('trade_health') or 'STABLE'}"
        )
    warnings = "\n".join(f"⚠️ {html.escape(x)}" for x in r["warnings"]) or "✅ No critical concentration warnings"
    await message.answer(
        "🧭 <b>PORTFOLIO INTELLIGENCE</b>\n\n"
        f"Active: <b>{r['count']}</b> · LONG/SHORT: <b>{r['longs']}/{r['shorts']}</b>\n"
        f"Dominant exposure: <b>{r['dominant']}</b>\n"
        f"Estimated open result: <b>{r['open_r']:+.2f}R</b>\n"
        f"Effective / gross heat: <b>{r['risk_r']:.2f}R / {r['gross_risk_r']:.2f}R · {r['heat']}</b>\n"
        f"Protected risk: <b>{r['protected_r']:.2f}R</b>\n\n"
        + "\n".join(positions) + "\n\n<b>Risk intelligence</b>\n" + warnings,
        parse_mode="HTML")


@router.message(Command("dna"))
async def dna_handler(message: Message):
    r = engine.dna(message.from_user.id)
    strengths = "\n".join(f"✅ {html.escape(x)}" for x in r["strengths"]) or "• Still collecting positive patterns"
    weaknesses = "\n".join(f"⚠️ {html.escape(x)}" for x in r["weaknesses"]) or "✅ No major statistical weakness detected"
    sample = r["maturity"]
    text = (
        "🧬 <b>TRADE DNA</b>\n\n"
        f"Dataset: <b>{r['trades']} resolved trades · {sample}</b>\n\n"
        f"Best symbol: <b>{_segment(r['best_symbol'])}</b>\n"
        f"Best timeframe: <b>{_segment(r['best_timeframe'])}</b>\n"
        f"Best direction: <b>{_segment(r['best_side'])}</b>\n"
        f"Weakest symbol: <b>{_segment(r['worst_symbol'])}</b>\n\n"
        f"<b>Strengths</b>\n{strengths}\n\n<b>Improvement signals</b>\n{weaknesses}"
    )
    if not premium.status(message.from_user.id)["active"]:
        text += "\n\n👑 Premium roadmap: session DNA, setup fingerprints, similarity cohorts and personalized risk rules."
    await message.answer(text, parse_mode="HTML")


@router.message(Command("insights"))
async def insights_handler(message: Message):
    p = engine.performance(message.from_user.id)
    portfolio = engine.portfolio(message.from_user.id)
    dna = engine.dna(message.from_user.id)
    verdict = "POSITIVE EDGE" if p["expectancy"] > 0 and p["profit_factor"] > 1 else "EDGE NOT CONFIRMED"
    risk = portfolio["warnings"][0] if portfolio["warnings"] else "No critical portfolio warning"
    await message.answer(
        "🧠 <b>INTELLIGENCE BRIEF</b>\n\n"
        f"Edge status: <b>{verdict}</b>\n"
        f"Expectancy / PF: <b>{p['expectancy']:+.2f}R / {p['profit_factor']:.2f}</b>\n"
        f"Best current cohort: <b>{_segment(dna['best_timeframe'])}</b>\n"
        f"Portfolio: <b>{portfolio['count']} active · {portfolio['heat']} heat · {portfolio['open_r']:+.2f}R</b>\n"
        f"Primary risk: <b>{html.escape(risk)}</b>\n\n"
        "Next actions:\n"
        "• Protect portfolio-level risk before adding correlated exposure.\n"
        "• Judge the system by expectancy and profit factor, not one signal.\n"
        "• Use /performance, /portfolio and /dna after every meaningful sample increase.",
        parse_mode="HTML")
