from utils.price import fmt_price, fmt_number


class Report:
    def ai_summary(self, data):
        direction = data["direction"]
        summary = [
            f"• Market bias: {data['market_bias']}.",
            f"• Primary scenario: {direction} ({data['score']}/100).",
            f"• Alternative scenario: {data['alternative_scenario']} ({data['alternative_score']}/100).",
            f"• Directional edge: {data['directional_edge']:+.1f} points.",
            f"• Execution status: {data['execution_status']}.",
            f"• Direction / Entry / Risk / Readiness: {data['direction_score']}/{data['entry_quality']}/{data['risk_quality']}/{data['execution_readiness']}.",
        ]
        if "Counter-trend" in " ".join(data["reasons"]):
            summary.append(f"• The {direction} idea is counter-trend and needs stronger confirmation.")
        else:
            summary.append(f"• Trend context supports the {direction} scenario.")
        summary.append(f"• Price location: {data['premium']['zone'].split(' ', 1)[-1]} ({data['premium']['premium']}% of range).")
        if data["triggers"]:
            summary.append(f"• Best next confirmation: {data['triggers'][0]}.")
        return "\n".join(summary)

    def probability_summary(self, data):
        exact = data.get("historical_probability") or {}
        similar = data.get("similar_stats") or {}
        exact_samples = int(exact.get("samples") or 0)
        similar_samples = int(similar.get("samples") or 0)
        if exact_samples >= 5:
            return (
                f"Exact setup samples: {exact_samples} | Reliability: {exact.get('reliability', 'Insufficient')}\n"
                f"TP1 {exact.get('tp1_rate', 0)}% · TP2 {exact.get('tp2_rate', 0)}% · "
                f"TP3 {exact.get('tp3_rate', 0)}% · Stop {exact.get('stop_rate', 0)}%"
            )
        if similar_samples:
            return (
                f"Similar completed setups: {similar_samples} | Reliability: {similar.get('reliability', 'Insufficient')}\n"
                f"TP1 {similar.get('tp1_rate', 0)}% · TP2 {similar.get('tp2_rate', 0)}% · "
                f"TP3 {similar.get('tp3_rate', 0)}% · Stop {similar.get('stop_rate', 0)}%"
            )
        return "Collecting completed setup history. No statistical probability is available yet."

    def build(self, data):
        triggers = "\n".join(f"• {item}" for item in data.get("triggers", [])) or "• Setup is ready under current conditions"
        alt = "\n".join(f"• {item}" for item in data.get("alternative_conditions", []))
        signal_text = data.get("signal_id") or "not recorded as executable trade"
        observation_text = data.get("observation_id") or "not recorded"
        reasons = "\n".join(data["reasons"]) or "⚪ No decisive confluence"
        p = fmt_price
        probability_text = self.probability_summary(data)
        return f"""
📊 <b>Liquidity Vision</b>

━━━━━━━━━━━━━━━━━━

🧭 <b>Market Bias</b>
{data['market_bias']}

🎯 <b>Primary Scenario</b>
{data['direction']} — {data['recommendation']}

🔁 <b>Alternative Scenario</b>
{data['alternative_scenario']} — {data['alternative_score']}/100

⚖️ <b>Directional Balance</b>
LONG {data['long_score']} / SHORT {data['short_score']}
Edge: {data['directional_edge']:+.1f}

🎬 <b>Execution Status</b>
{data['execution_status']}

📊 <b>Execution Intelligence</b>
Direction: {data['direction_score']}/100
Entry Quality: {data['entry_quality']}/100
Risk Quality: {data['risk_quality']}/100
Readiness: {data['execution_readiness']}/100

━━━━━━━━━━━━━━━━━━

💰 Price
{p(data['price'])}

📈 Trend
{data['trend']}

🏗 Structure
{data['structure']}

🔨 BOS
{data['bos']}

🔄 CHOCH
{data['choch']}

━━━━━━━━━━━━━━━━━━

💧 External Liquidity
{data['liquidity']}

🌊 Liquidity Event
{data['sweep']}

📦 Order Block
{data['order_block']}

🧱 Breaker
{data['breaker']}

🛡 Mitigation
{data['mitigation']}

🟨 FVG
{data['fvg']}

━━━━━━━━━━━━━━━━━━

💎 Dealing Range Location
{data['premium']['zone']}

Range Position
{data['premium']['premium']}%

Range Low / EQ / High
{p(data['premium']['low'])} / {p(data['premium']['equilibrium'])} / {p(data['premium']['high'])}

━━━━━━━━━━━━━━━━━━

📉 EMA50 / EMA200
{p(data['ema50'])} / {p(data['ema200'])}

⚡ RSI
{data['rsi']:.2f}

📊 MACD
{data['macd']}

📦 Volume
{data['volume']}

🚀 Displacement
{data['displacement']}

ATR
{p(data['atr']['atr'])}

━━━━━━━━━━━━━━━━━━

🎯 Trade Plan
Current Entry: {p(data['entry'])}
Preferred Zone: {p(data['preferred_entry_low'])} - {p(data['preferred_entry_high'])}
Stop: {p(data['stop'])}
TP1: {p(data['tp1'])}
TP2: {p(data['tp2'])}
TP3: {p(data['tp3'])}
RR: 1:{fmt_number(data['rr'])}

━━━━━━━━━━━━━━━━━━

📐 Setup Score
{data['score']}/100

🔎 Confirmations
{data['confirmations']}

⭐ Trade Quality
{data['quality']}

🏅 Scanner Rank
{data['ranking_score']}

━━━━━━━━━━━━━━━━━━

🧠 Confluence & Risks
{reasons}

━━━━━━━━━━━━━━━━━━

🔔 Activation Conditions
{triggers}

━━━━━━━━━━━━━━━━━━

🔄 Alternative Activation
{alt}

━━━━━━━━━━━━━━━━━━

🤖 AI Summary
{self.ai_summary(data)}

━━━━━━━━━━━━━━━━━━

📚 <b>Historical Intelligence</b>
{probability_text}

━━━━━━━━━━━━━━━━━━

🧾 Signal ID
{signal_text}

━━━━━━━━━━━━━━━━━━

⚠️ <i>Not financial advice. Always use proper risk management.</i>
"""
