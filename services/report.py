class Report:

    def ai_summary(self, data):

        summary = []

        if "Bullish" in data["trend"]:
            summary.append(
                "• Higher timeframe trend is bullish."
            )
        else:
            summary.append(
                "• Higher timeframe trend is bearish."
            )

        if "Bullish" in data["structure"]:
            summary.append(
                "• Market structure supports continuation."
            )
        elif "Bearish" in data["structure"]:
            summary.append(
                "• Market structure remains bearish."
            )

        if "Bullish" in data["choch"]:
            summary.append(
                "• Bullish CHOCH detected."
            )

        if "Sell Side Sweep" in data["sweep"]:
            summary.append(
                "• Sell-side liquidity has been swept."
            )

        if "Bullish" in data["order_block"]:
            summary.append(
                "• Price is reacting from a Bullish Order Block."
            )

        if "Bullish" in data["breaker"]:
            summary.append(
                "• Bullish Breaker Block is active."
            )

        if "Bullish" in data["mitigation"]:
            summary.append(
                "• Mitigation Block remains valid."
            )

        if "Bullish" in data["fvg"]:
            summary.append(
                "• Bullish Fair Value Gap detected."
            )

        if "Discount" in data["premium"]["zone"]:
            summary.append(
                "• Price trades in Discount."
            )

        if "Bullish" in data["displacement"]:
            summary.append(
                "• Strong bullish displacement."
            )

        if "Spike" in data["volume"]:
            summary.append(
                "• Volume expansion confirms momentum."
            )

        if not summary:
            summary.append(
                "• No strong Smart Money confirmation."
            )

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

🎲 Probability

{data["probability"]}%

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
