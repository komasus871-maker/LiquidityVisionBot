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

    def build(self, data):
        triggers = "\n".join(f"• {item}" for item in data.get("triggers", [])) or "• Setup is ready under current conditions"
        alt = "\n".join(f"• {item}" for item in data.get("alternative_conditions", []))
        signal_text = data.get("signal_id") or "not recorded as executable trade"
        reasons = "\n".join(data["reasons"]) or "⚪ No decisive confluence"
        p = fmt_price
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

🧾 Signal ID
{signal_text}

━━━━━━━━━━━━━━━━━━

⚠️ <i>Not financial advice. Always use proper risk management.</i>
"""
