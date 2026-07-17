from html import escape
from utils.price import fmt_price, fmt_number
from services.scenario_engine import ScenarioEngine


class Report:
    @staticmethod
    def _component_lines(items, empty="• None", limit=3):
        if not items:
            return empty
        return "\n".join(
            f"• {escape(str(item.get('label', 'Factor')))}"
            for item in items[:limit]
        )

    @staticmethod
    def _reason_lines(reasons, prefix, empty, limit=3):
        selected = [str(x).replace(prefix, "").strip() for x in reasons if str(x).startswith(prefix)]
        if not selected:
            return empty
        return "\n".join(f"• {escape(x)}" for x in selected[:limit])

    def build(self, data):
        p = fmt_price
        strengths = self._reason_lines(data.get("reasons", []), "✅ ", "• No decisive confirmation")
        risks = self._reason_lines(data.get("reasons", []), "⚠️ ", "• No major secondary risk")
        hard = self._reason_lines(data.get("reasons", []), "⛔ ", "", limit=2)
        if hard:
            risks = f"{risks}\n{hard}" if risks else hard
        triggers = data.get("triggers", []) or ["No additional trigger required"]
        trigger_text = "\n".join(f"• {escape(str(x))}" for x in triggers[:3])
        regime = data.get("market_regime", {}) or {}
        current = data.get("current_price", data.get("price", 0))
        entry_type = str(data.get("entry_type", "UNKNOWN")).replace("_", " ").title()
        plan_mode = str(data.get("plan_mode") or "TRADE_PLAN")
        plan_title = "Trade Plan" if plan_mode == "TRADE_PLAN" else "Area of Interest"
        quality = escape(str(data.get("trade_quality_stars") or data.get("quality") or "⭐☆☆☆☆"))
        entry_reasons = data.get("entry_reasons") or []
        reason_text = "\n".join(f"• {escape(str(x))}" for x in entry_reasons[:3]) or "• Volatility-adjusted execution area"
        context = data.get("global_context") or {}
        context_text = escape(str(context.get("summary") or "No broad-market adjustment."))
        take_text = "✅ YES" if data.get("would_take_trade") else "❌ NO"
        conviction = data.get("conviction") or {}
        action = escape(str(conviction.get("action") or data.get("decision_action") or "WATCH"))
        bull_score = fmt_number(conviction.get("bull_score", 0), 1)
        bear_score = fmt_number(conviction.get("bear_score", 0), 1)
        directional_conviction = fmt_number(conviction.get("directional_confidence", 0), 1)
        execution_conviction = fmt_number(conviction.get("execution_confidence", 0), 1)
        support = conviction.get("strongest_support") or {}
        opposition = conviction.get("strongest_opposition") or {}
        support_text = escape(str(support.get("label") or "No decisive support"))
        opposition_text = escape(str(opposition.get("label") or "No decisive opposition"))
        unified = data.get("unified_decision") or {}
        unified_support = "\n".join(
            f"• {escape(str(x.get('label', 'Factor')))}: {float(x.get('value') or 0):+.1f}"
            for x in (unified.get("top_support") or [])[:2]
        ) or "• No decisive unified support"
        unified_opposition = "\n".join(
            f"• {escape(str(x.get('label', 'Factor')))}: {float(x.get('value') or 0):+.1f}"
            for x in (unified.get("top_opposition") or [])[:2]
        ) or "• No major unified opposition"
        memory = data.get("market_memory") or {}
        memory_text = "\n".join(f"• {escape(str(x))}" for x in (memory.get("changes") or [])[:3]) or f"• {escape(str(memory.get('summary') or 'Collecting snapshots.'))}"
        brain = data.get("decision_brain") or {}
        ev = brain.get("expected_value") or data.get("expected_value") or {}
        reasoning = "\n".join(f"{idx}. {escape(str(line))}" for idx, line in enumerate((brain.get("reasoning") or [])[:4], 1)) or "1. Collecting decision context."

        return f"""
📊 <b>{escape(str(data.get('symbol', 'Liquidity Vision')).upper())} · {escape(str(data.get('timeframe', '')).upper())}</b>

{escape(str(data.get('market_bias', 'Unknown')))}
{escape(str(data.get('execution_status', 'WATCHLIST')))}
🚦 <b>Decision Engine: {action}</b>
Conviction: <b>{escape(str(conviction.get('confidence_band', 'LOW')))}</b> · Direction {directional_conviction}% · Execution {execution_conviction}%
🟢 Bulls <b>{bull_score}</b> : <b>{bear_score}</b> Bears 🔴
Strongest support: {support_text}
Strongest opposition: {opposition_text}

🧠 <b>Decision Report v8.0</b>
Action: <b>{escape(str(brain.get('action', unified.get('action', 'WAIT'))))}</b> · Decision <b>{fmt_number(brain.get('score', unified.get('score', 0)), 1)}/100</b>
Direction: {fmt_number(brain.get('direction_score', data.get('direction_score', 0)), 1)}/100 · Execution: {fmt_number(brain.get('execution_score', data.get('execution_readiness', 0)), 1)}/100
Expected value: <b>{float(ev.get('expected_r') or 0):+.2f}R</b> · {escape(str(ev.get('band', 'UNKNOWN')))} · {escape(str(ev.get('confidence', 'MODEL-BASED')))}
Primary reason: {escape(str(brain.get('primary_reason') or unified.get('reason') or 'Awaiting context.'))}
Next condition: {escape(str(brain.get('next_condition') or 'Material confirmation'))}

<b>Reasoning chain</b>
{reasoning}

<b>Decision factors</b>
Support
{unified_support}
Opposition
{unified_opposition}

🕰 <b>Market Memory</b>
{escape(str(memory.get('state', 'LEARNING')))} · {int(memory.get('samples') or 0)} snapshots
{memory_text}

{quality} <b>Trade Quality</b>
⭐ Setup: {fmt_number(data.get('score', 0), 1)}/100 · Grade {escape(str(data.get('ai_grade', 'N/A')))}
🧭 Direction: {fmt_number(data.get('direction_score', 0), 1)}/100
🌍 Regime: {escape(str(regime.get('label', 'Unknown')))}
⚖️ Risk multiplier: {fmt_number(regime.get('risk_multiplier', 1.0), 2)}x

━━━━━━━━━━━━━━━━━━

💰 <b>{plan_title}</b>
Current: {p(current)}
Planned Entry: {p(data.get('entry'))} ({escape(entry_type)})
Zone: {p(data.get('preferred_entry_low'))} – {p(data.get('preferred_entry_high'))}
Invalidation: {p(data.get('stop'))}
Targets: {p(data.get('tp1'))} / {p(data.get('tp2'))} / {p(data.get('tp3'))}
RR: 1:{fmt_number(data.get('rr', 0), 1)}

📍 <b>Why this zone</b>
{reason_text}

🌐 <b>Broad market context</b>
{context_text}

🤔 <b>Would the system take it now?</b>
{take_text}

━━━━━━━━━━━━━━━━━━

✅ <b>Main strengths</b>
{strengths}

⚠️ <b>Main risks</b>
{risks}

🔔 <b>Wait for</b>
{trigger_text}

━━━━━━━━━━━━━━━━━━

🧠 <b>Verdict</b>
{escape(str(data.get('final_verdict', 'Observe current conditions.')))}

⚠️ <i>Not financial advice. Use proper risk management.</i>
""".strip()

    def why_not(self, data):
        blockers = [str(x).replace("⛔ ", "").strip() for x in (data.get("reasons") or []) if str(x).startswith("⛔ ")]
        risks = [str(x).replace("⚠️ ", "").strip() for x in (data.get("reasons") or []) if str(x).startswith("⚠️ ")]
        reasons = blockers + risks
        body = "\n".join(f"• {escape(x)}" for x in reasons[:5]) or "• The setup does not yet have enough execution edge."
        triggers = data.get("triggers") or ["Wait for stronger confirmation"]
        next_steps = "\n".join(f"• {escape(str(x))}" for x in triggers[:3])
        return f"""🚫 <b>Why NOT? — {escape(str(data.get('symbol', '')).upper())} · {escape(str(data.get('timeframe', '')).upper())}</b>

The directional idea may still be valid, but the activation trigger is still missing. Execution is delayed because:
{body}

<b>What would change the decision</b>
{next_steps}

Waiting protects expectancy."""

    def technical(self, data):
        p = fmt_price
        regime = data.get("market_regime", {}) or {}
        premium = data.get("premium", {}) or {}
        return f"""
📐 <b>Technical Details — {escape(str(data.get('symbol', '')).upper())} · {escape(str(data.get('timeframe', '')).upper())}</b>

📈 <b>Trend</b>: {escape(str(data.get('trend', 'Unknown')))}
🏗 <b>Structure</b>: {escape(str(data.get('structure', 'Unknown')))}
🔨 <b>BOS</b>: {escape(str(data.get('bos', 'Unknown')))}
🔄 <b>CHOCH</b>: {escape(str(data.get('choch', 'Unknown')))}

💧 <b>Liquidity</b>: {escape(str(data.get('liquidity', 'Unknown')))}
🌊 <b>Liquidity event</b>: {escape(str(data.get('sweep', 'Unknown')))}
📦 <b>Order Block</b>: {escape(str(data.get('order_block', 'Unknown')))}
🧱 <b>Breaker</b>: {escape(str(data.get('breaker', 'Unknown')))}
🛡 <b>Mitigation</b>: {escape(str(data.get('mitigation', 'Unknown')))}
🟨 <b>FVG</b>: {escape(str(data.get('fvg', 'Unknown')))}

💎 <b>Dealing Range</b>
Location: {escape(str(premium.get('zone', 'Unknown')))}
Position: {fmt_number(premium.get('premium', 50), 2)}%
Low / EQ / High: {p(premium.get('low'))} / {p(premium.get('equilibrium'))} / {p(premium.get('high'))}

📉 <b>EMA50 / EMA200</b>: {p(data.get('ema50'))} / {p(data.get('ema200'))}
⚡ <b>RSI</b>: {fmt_number(data.get('rsi', 0), 2)}
📊 <b>MACD</b>: {escape(str(data.get('macd', 'Unknown')))}
📦 <b>Volume</b>: {escape(str(data.get('volume', 'Unknown')))}
🚀 <b>Displacement</b>: {escape(str(data.get('displacement', 'Unknown')))}
📏 <b>ATR</b>: {p((data.get('atr') or {}).get('atr'))}

🌍 <b>Regime diagnostics</b>
Confidence: {fmt_number(regime.get('confidence', 0), 1)}/100
Execution mode: {escape(str(regime.get('execution_mode', 'OBSERVE')))}
Trend strength: {fmt_number(regime.get('trend_strength', 0), 1)}/100
Path efficiency: {fmt_number(regime.get('efficiency', 0), 1)}%
Volatility: {escape(str(regime.get('volatility_state', 'NORMAL')))} ({fmt_number(regime.get('volatility_percentile', 50), 1)}th pct)
""".strip()

    def scenarios(self, data):
        path = data.get("expected_path") or []
        path_text = "\n   ↓\n".join(escape(str(x)) for x in path) if path else "Observe current structure\n   ↓\nWait for confirmation\n   ↓\nTP1\n   ↓\nTP2\n   ↓\nTP3"
        alternatives = data.get("alternative_conditions") or []
        alternative = "\n".join(f"• {escape(str(x))}" for x in alternatives[:4]) or "• Opposite structure confirmation"
        invalidation = escape(str(data.get("stop")))
        return f"""
🧭 <b>Expected Path — {escape(str(data.get('symbol', '')).upper())} · {escape(str(data.get('timeframe', '')).upper())}</b>

<b>Primary path</b>
{path_text}

<b>Alternative path</b>
{alternative}

🛑 <b>Invalidation reference</b>
{invalidation}

💡 <b>Why this trade exists</b>
{escape(str(data.get('why_trade_exists', 'No directional thesis is trusted yet.')))}
""".strip()

    def history(self, data):
        intelligence = data.get("historical_intelligence") or {}
        closest = intelligence.get("closest_case") or {}
        samples = int(intelligence.get("samples") or 0)
        if not intelligence.get("display_probabilities"):
            body = (
                f"Sample is still insufficient: <b>{samples}</b> comparable completed setups.\n"
                "Probabilities are intentionally hidden until the effective sample is statistically usable."
            )
        else:
            expected = intelligence.get("expected_r")
            expected_text = "unknown" if expected is None else f"{float(expected):+.2f}R"
            body = (
                f"Weighted matches: <b>{samples}</b> · effective {fmt_number(intelligence.get('effective_samples', 0), 1)}\n"
                f"Average similarity: <b>{fmt_number(intelligence.get('average_similarity', 0), 1)}%</b>\n"
                f"Expected result: <b>{expected_text}</b>\n"
                f"Reliability: <b>{escape(str(intelligence.get('reliability', 'Insufficient')))}</b>"
            )
            if closest:
                body += (
                    f"\n\nClosest case: <b>#{closest.get('signal_id')}</b> "
                    f"{escape(str(closest.get('symbol', '')))} {escape(str(closest.get('side', '')))} · "
                    f"{fmt_number(closest.get('similarity', 0), 1)}% similarity · "
                    f"{escape(str(closest.get('status', 'UNKNOWN')))}"
                )
        return f"📚 <b>Historical Intelligence — {escape(str(data.get('symbol', '')).upper())}</b>\n\n{body}"
