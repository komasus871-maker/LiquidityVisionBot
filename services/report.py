class Report:
    def ai_summary(self, data):
        summary = []
        direction = data["direction"]
        long = direction == "LONG"
        summary.append(f"• Market bias: {data['market_bias']}.")
        summary.append(f"• Execution status: {data['execution_status']}.")
        if "Counter-trend" in " ".join(data["reasons"]):
            summary.append(f"• The {direction} idea is counter-trend and requires stronger confirmation.")
        else:
            summary.append(f"• Trend context supports the {direction} scenario.")
        if "Bullish" in data["structure"] and long or "Bearish" in data["structure"] and not long:
            summary.append("• Market structure agrees with the directional bias.")
        if "Sell Side Sweep" in data["sweep"]:
            summary.append("• Sell-side liquidity has been swept.")
        elif "Buy Side Sweep" in data["sweep"]:
            summary.append("• Buy-side liquidity has been swept.")
        if "Bullish" in data["fvg"]:
            summary.append("• A bullish Fair Value Gap is active.")
        elif "Bearish" in data["fvg"]:
            summary.append("• A bearish Fair Value Gap is active.")
        summary.append(f"• Price location: {data['premium']['zone'].replace('🔴 ', '').replace('🟢 ', '').replace('🟡 ', '')} ({data['premium']['premium']}% of range).")
        if data["triggers"]:
            summary.append(f"• Best next confirmation: {data['triggers'][0]}.")
        return "\n".join(summary)

    def build(self, data):
        triggers = "\n".join(f"• {item}" for item in data.get("triggers", [])) or "• Setup is ready under current conditions"
        signal_text = data.get("signal_id") or "not recorded as executable trade"
        return f"""
📊 <b>Liquidity Vision</b>

━━━━━━━━━━━━━━━━━━

🧭 <b>Market Bias</b>
{data['market_bias']}

🎬 <b>Execution Status</b>
{data['execution_status']}

💡 <b>Recommendation</b>
{data['recommendation']}

━━━━━━━━━━━━━━━━━━

💰 Price
{data['price']:.2f}

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
{data['premium']['low']} / {data['premium']['equilibrium']} / {data['premium']['high']}

━━━━━━━━━━━━━━━━━━

📉 EMA50 / EMA200
{data['ema50']:.2f} / {data['ema200']:.2f}

⚡ RSI
{data['rsi']:.2f}

📊 MACD
{data['macd']}

📦 Volume
{data['volume']}

🚀 Displacement
{data['displacement']}

ATR
{data['atr']['atr']}

━━━━━━━━━━━━━━━━━━

🎯 Trade Plan
Entry: {data['entry']:.2f}
Stop: {data['stop']:.2f}
TP1: {data['tp1']:.2f}
TP2: {data['tp2']:.2f}
TP3: {data['tp3']:.2f}
RR: 1:{data['rr']}

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
{"\n".join(data['reasons'])}

━━━━━━━━━━━━━━━━━━

🔔 Activation Conditions
{triggers}

━━━━━━━━━━━━━━━━━━

🤖 AI Summary
{self.ai_summary(data)}

━━━━━━━━━━━━━━━━━━

🧾 Signal ID
{signal_text}

━━━━━━━━━━━━━━━━━━

⚠️ <i>Not financial advice. Always use proper risk management.</i>
"""
