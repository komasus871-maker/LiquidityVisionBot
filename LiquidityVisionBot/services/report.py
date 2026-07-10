from utils.price import fmt_price, fmt_number


class Report:
    def ai_summary(self, data):
        direction = data["direction"]
        reasons = data.get("reasons", [])
        positives = [r.replace("✅", "", 1).strip() for r in reasons if r.startswith("✅")]
        warnings = [r.replace("⚠️", "", 1).strip() for r in reasons if r.startswith("⚠️")]
        blockers = [r.replace("⛔", "", 1).strip() for r in reasons if r.startswith("⛔")]
        location = data["premium"]["zone"].split(" ", 1)[-1]
        preferred = f"{fmt_price(data['preferred_entry_low'])}–{fmt_price(data['preferred_entry_high'])}"

        lines = [
            f"• <b>{direction}</b> is primary because {', '.join(positives[:3]).lower() if positives else 'the directional score has a measurable edge'}.",
            f"• Current execution status is <b>{data['execution_status']}</b>; direction is {data['direction_score']}/100 while entry quality is {data['entry_quality']}/100.",
            f"• Price is in <b>{location}</b>; preferred execution zone is <b>{preferred}</b>.",
        ]
        if blockers:
            lines.append(f"• Main blocker: <b>{blockers[0]}</b>.")
        elif warnings:
            lines.append(f"• Main risk: <b>{warnings[0]}</b>.")
        if data.get("triggers"):
            lines.append(f"• Best next confirmation: <b>{data['triggers'][0]}</b>.")
        lines.append(f"• Scenario invalidation is defined by Stop at <b>{fmt_price(data['stop'])}</b> or opposite structural confirmation.")
        return "\n".join(lines)

    def build(self, data):
        triggers = "\n".join(f"• {item}" for item in data.get("triggers", [])) or "• Setup is ready under current conditions"
        alt = "\n".join(f"• {item}" for item in data.get("alternative_conditions", []))
        signal_text = data.get("signal_id") or "not recorded as executable trade"
        reasons = "\n".join(data["reasons"]) or "⚪ No decisive confluence"
        probability = data.get("historical_probability") or {}
        if probability.get("sample_size", 0) >= 30:
            probability_text = (
                f"Samples: {probability['sample_size']}\n"
                f"TP1 {probability['tp1_rate']}% | TP2 {probability['tp2_rate']}% | "
                f"TP3 {probability['tp3_rate']}% | Stop {probability['stop_rate']}%"
            )
        else:
            probability_text = (
                f"Samples: {probability.get('sample_size', 0)}\n"
                "Collecting data — minimum 30 completed similar setups required."
            )
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

📊 <b>Historical Probability</b>
{probability_text}

━━━━━━━━━━━━━━━━━━

🤖 <b>AI Analyst Summary</b>
{self.ai_summary(data)}

━━━━━━━━━━━━━━━━━━

🧾 Signal ID
{signal_text}

━━━━━━━━━━━━━━━━━━

⚠️ <i>Not financial advice. Always use proper risk management.</i>
"""
