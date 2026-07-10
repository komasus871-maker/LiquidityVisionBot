class Report:

    def ai_summary(self, data):

        summary = []
        direction = data.get("direction", "LONG")
        long = direction == "LONG"
        trend_bull = "Bullish" in data["trend"]
        structure_bull = "Bullish" in data["structure"]

        if (long and trend_bull) or (not long and not trend_bull):
            summary.append(f"• Higher timeframe trend is aligned with the {direction} setup.")
        else:
            summary.append(f"• Higher timeframe trend conflicts with the {direction} setup; this is counter-trend.")

        if (long and structure_bull) or (not long and not structure_bull):
            summary.append(f"• Market structure supports the {direction} direction.")
        else:
            summary.append(f"• Market structure does not fully confirm the {direction} direction.")

        if "Bullish" in data["choch"]:
            summary.append("• Bullish CHOCH detected.")
        elif "Bearish" in data["choch"]:
            summary.append("• Bearish CHOCH detected.")

        if "Sell Side Sweep" in data["sweep"]:
            summary.append("• Sell-side liquidity has been swept.")
        elif "Buy Side Sweep" in data["sweep"]:
            summary.append("• Buy-side liquidity has been swept.")

        if "Bullish" in data["order_block"]:
            summary.append("• Price is reacting from a Bullish Order Block.")
        elif "Bearish" in data["order_block"]:
            summary.append("• Price is reacting from a Bearish Order Block.")

        if "Bullish" in data["breaker"]:
            summary.append("• Bullish Breaker Block is active.")
        elif "Bearish" in data["breaker"]:
            summary.append("• Bearish Breaker Block is active.")

        if "Bullish" in data["mitigation"]:
            summary.append("• Bullish Mitigation Block remains valid.")
        elif "Bearish" in data["mitigation"]:
            summary.append("• Bearish Mitigation Block remains valid.")

        if "Bullish" in data["fvg"]:
            summary.append("• Bullish Fair Value Gap detected.")
        elif "Bearish" in data["fvg"]:
            summary.append("• Bearish Fair Value Gap detected.")

        if "Discount" in data["premium"]["zone"]:
            summary.append("• Price trades in Discount.")
        elif "Premium" in data["premium"]["zone"]:
            summary.append("• Price trades in Premium.")

        if "Weak Displacement" in data["displacement"]:
            summary.append("• Displacement is weak, so momentum confirmation is limited.")
        elif "Bullish" in data["displacement"]:
            summary.append("• Strong bullish displacement is present.")
        elif "Bearish" in data["displacement"]:
            summary.append("• Strong bearish displacement is present.")

        if data.get("volume_ratio", 1.0) < 0.8:
            summary.append("• Relative volume is below average.")
        elif "Spike" in data["volume"]:
            summary.append("• Volume expansion confirms momentum.")

        return "\n".join(summary)

    def build(self, data):

        return f"""
📊 <b>Liquidity Vision</b>

━━━━━━━━━━━━━━━━━━

💰 Price
{data["price"]:.2f}

━━━━━━━━━━━━━━━━━━

📈 Trend

{data["trend"]}

🏗 Structure

{data["structure"]}

🔨 BOS

{data["bos"]}

🔄 CHOCH

{data["choch"]}

━━━━━━━━━━━━━━━━━━

💧 Liquidity

{data["liquidity"]}

🌊 Sweep

{data["sweep"]}

━━━━━━━━━━━━━━━━━━

📦 Order Block

{data["order_block"]}

🧱 Breaker

{data["breaker"]}

🛡 Mitigation

{data["mitigation"]}

🟨 FVG

{data["fvg"]}

━━━━━━━━━━━━━━━━━━

💎 Premium / Discount

{data["premium"]["zone"]}

Premium %

{data["premium"]["premium"]}%

Equilibrium

{data["premium"]["equilibrium"]}

━━━━━━━━━━━━━━━━━━

📉 EMA50

{data["ema50"]:.2f}

📉 EMA200

{data["ema200"]:.2f}

⚡ RSI

{data["rsi"]:.2f}

📊 MACD

{data["macd"]}

📦 Volume

{data["volume"]}

🚀 Displacement

{data["displacement"]}

ATR

{data["atr"]["atr"]}

━━━━━━━━━━━━━━━━━━

🎯 Entry

{data["entry"]:.2f}

🛑 Stop

{data["stop"]:.2f}

🎯 TP1

{data["tp1"]:.2f}

🎯 TP2

{data["tp2"]:.2f}

🎯 TP3

{data["tp3"]:.2f}

⚖ RR

1:{data["rr"]}

━━━━━━━━━━━━━━━━━━

📐 Setup Score

{data["score"]}/100

🔎 Confirmations

{data["confirmations"]}

⭐ Trade Quality

{data["quality"]}

💡 Recommendation

{data["recommendation"]}

━━━━━━━━━━━━━━━━━━

🧠 Confluence

{"\n".join(data["reasons"])}

━━━━━━━━━━━━━━━━━━

🤖 AI Summary

{self.ai_summary(data)}

━━━━━━━━━━━━━━━━━━

🧾 Signal ID
{data.get("signal_id") or "not saved (WAIT)"}

━━━━━━━━━━━━━━━━━━

⚠️ <i>Not financial advice.
Always use proper risk management.</i>
"""
