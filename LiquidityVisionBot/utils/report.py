class Report:

    @staticmethod
    def telegram(data):

        return f"""
📊 <b>Liquidity Vision Analysis</b>

🪙 Symbol:
{data["symbol"]}

━━━━━━━━━━━━━━━━

💰 Price:
{data["price"]:.2f}

📈 Trend:
{data["trend"]}

🔥 Trend Strength:
{data["trend_strength"]}

🏗 Structure:
{data["bos"]}

🔄 CHOCH:
{data["choch"]}

💧 Liquidity:
{data["liquidity"]}

🌊 Sweep:
{data["sweep"]}

📦 Order Block:
{data["order_block"]}

🟨 Fair Value Gap:
{data["fvg"]}

━━━━━━━━━━━━━━━━

📊 EMA50:
{data["ema50"]:.2f}

📊 EMA200:
{data["ema200"]:.2f}

⚡ RSI:
{data["rsi"]:.2f}

📉 MACD:
{data["macd"]}

📦 Volume:
{data["volume"]}

📏 ATR:
{data["atr"]:.2f}

━━━━━━━━━━━━━━━━

🎯 Entry:
{data["entry"]}

🛑 Stop Loss:
{data["stop"]}

🎯 TP1:
{data["tp1"]}

🎯 TP2:
{data["tp2"]}

🎯 TP3:
{data["tp3"]}

━━━━━━━━━━━━━━━━

📈 RR:
1:{data["rr"]:.2f}

🎲 Probability:
{data["probability"]}%

⭐ Score:
{data["score"]}/100

🚀 Recommendation

{data["recommendation"]}
"""