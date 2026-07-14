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

        return f"""
📊 <b>{escape(str(data.get('symbol', 'Liquidity Vision')).upper())} · {escape(str(data.get('timeframe', '')).upper())}</b>

{escape(str(data.get('market_bias', 'Unknown')))}
{escape(str(data.get('execution_status', 'WATCHLIST')))}

{quality} <b>Trade Quality</b>
⭐ Setup: {fmt_number(data.get('score', 0), 1)}/100 · Grade {escape(str(data.get('ai_grade', 'N/A')))}
🧭 Direction: {fmt_number(data.get('direction_score', 0), 1)}/100
🌍 Regime: {escape(str(regime.get('label', 'Unknown')))}
⚖️ Risk multiplier: {fmt_number(regime.get('risk_multiplier', 1.0), 2)}x

━━━━━━━━━━━━━━━━━━

💰 <b>{plan_title}</b>
Current: {p(current)}
Planned level: {p(data.get('entry'))} ({escape(entry_type)})
Zone: {p(data.get('preferred_entry_low'))} – {p(data.get('preferred_entry_high'))}
Invalidation: {p(data.get('stop'))}
Targets: {p(data.get('tp1'))} / {p(data.get('tp2'))} / {p(data.get('tp3'))}
RR: 1:{fmt_number(data.get('rr', 0), 1)}

📍 <b>Why this zone</b>
{reason_text}

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
        path_text = "\n   ↓\n".join(escape(str(x)) for x in path) or "Observe current structure"
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
        exact = data.get("historical_probability") or {}
        similar = data.get("similar_stats") or {}
        exact_samples = int(exact.get("samples") or 0)
        similar_samples = int(similar.get("samples") or 0)
        if exact_samples >= 5:
            body = (
                f"Exact samples: <b>{exact_samples}</b>\n"
                f"TP1 {exact.get('tp1_rate', 0)}% · TP2 {exact.get('tp2_rate', 0)}% · "
                f"TP3 {exact.get('tp3_rate', 0)}% · Stop {exact.get('stop_rate', 0)}%\n"
                f"Reliability: <b>{escape(str(exact.get('reliability', 'Insufficient')))}</b>"
            )
        elif similar_samples:
            body = (
                f"Similar completed setups: <b>{similar_samples}</b>\n"
                f"TP1 {similar.get('tp1_rate', 0)}% · TP2 {similar.get('tp2_rate', 0)}% · "
                f"TP3 {similar.get('tp3_rate', 0)}% · Stop {similar.get('stop_rate', 0)}%\n"
                f"Reliability: <b>{escape(str(similar.get('reliability', 'Insufficient')))}</b>"
            )
        else:
            body = "No statistically usable completed sample yet. The system is collecting outcomes."
        return f"📚 <b>Historical Intelligence — {escape(str(data.get('symbol', '')).upper())}</b>\n\n{body}"
