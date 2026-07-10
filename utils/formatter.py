def format_analysis(symbol, data):

    text = f"""
📊 {symbol}

💲 Price: {data['price']}

📈 Trend: {data['trend']}

⭐ Score: {data['score']}/100

RSI: {data['rsi']}

EMA50: {data['ema50']}

EMA200: {data['ema200']}

MACD: {data['macd']}

Signal: {data['signal']}

Reasons:

"""

    for reason in data["reasons"]:
        text += f"• {reason}\n"

    return text