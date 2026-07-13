from utils.price import fmt_price, fmt_number
from services.scenario_engine import ScenarioEngine


class Report:
    @staticmethod
    def _component_lines(items, empty="• None"):
        if not items:
            return empty
        return "\n".join(
            f"• {item['label']}: {item['value']:+.1f}" for item in items
        )

    def ai_summary(self, data):
        direction = data["direction"]
        summary = [
            f"• Market direction: {data['market_bias']} ({data['direction_score']}/100).",
            f"• Execution bias: {data.get('execution_bias', 'NEUTRAL / OBSERVE')}.",
            f"• Alternative scenario: {data['alternative_scenario']} ({data['alternative_score']}/100).",
            f"• Directional edge: {data['directional_edge']:+.1f} points.",
            f"• Entry / Risk / Readiness: {data['entry_quality']}/{data['risk_quality']}/{data['execution_readiness']}.",
        ]
        if data.get("exhaustion_risk"):
            summary.append("• Momentum may be directionally valid, but continuation is currently stretched.")
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
        breakdown = data.get("direction_breakdown", {})
        breakdown_text = "\n".join(
            f"{name}: {value:+.1f}" for name, value in breakdown.items()
        ) or "No breakdown available"
        drivers = self._component_lines(data.get("strongest_drivers", []))
        blockers = self._component_lines(data.get("biggest_blockers", []), "• No major negative component")
        scenarios = ScenarioEngine.build(data)
        primary_path = "\n".join(f"{idx}. {item}" for idx, item in enumerate(scenarios["primary"], 1))
        alternative_path = "\n".join(f"{idx}. {item}" for idx, item in enumerate(scenarios["alternative"], 1))

        return f"""
📊 <b>Liquidity Vision</b>

━━━━━━━━━━━━━━━━━━

🧭 <b>Market Direction</b>
{data['market_bias']}
Direction score: {data['direction_score']}/100

⚡ <b>Execution Bias</b>
{data.get('execution_bias', 'NEUTRAL / OBSERVE')}
{data['execution_status']}

🎯 <b>Primary Scenario</b>
{data['direction']} — {data['recommendation']}

🔁 <b>Alternative Scenario</b>
{data['alternative_scenario']} — {data['alternative_score']}/100

⚖️ <b>Directional Balance</b>
LONG {data['long_score']} / SHORT {data['short_score']}
Edge: {data['directional_edge']:+.1f}

📊 <b>Execution Intelligence</b>
Entry Quality: {data['entry_quality']}/100
Risk Quality: {data['risk_quality']}/100
Readiness: {data['execution_readiness']}/100
AI Grade: {data.get('ai_grade', 'N/A')}

━━━━━━━━━━━━━━━━━━

🧠 <b>Final Verdict</b>
{data.get('final_verdict', 'Observe current conditions.')}

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

🧮 <b>Direction Breakdown</b>
{breakdown_text}

🚀 <b>Strongest Drivers</b>
{drivers}

🚧 <b>Biggest Blockers</b>
{blockers}

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

🧭 <b>Scenario Engine</b>

<b>Scenario A — Primary path</b>
{primary_path}

<b>Scenario B — Alternative path</b>
{alternative_path}

━━━━━━━━━━━━━━━━━━

🤖 AI Summary
{self.ai_summary(data)}

━━━━━━━━━━━━━━━━━━

📚 <b>Historical Intelligence</b>
{probability_text}

━━━━━━━━━━━━━━━━━━

🧾 Signal ID
{signal_text}

👁 Observation ID
{observation_text}

━━━━━━━━━━━━━━━━━━

⚠️ <i>Not financial advice. Always use proper risk management.</i>
"""
