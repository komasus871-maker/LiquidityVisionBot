def market_status(score):

    if score >= 90:

        return "🟢 VERY BULLISH"

    if score >= 75:

        return "🟢 BULLISH"

    if score >= 60:

        return "🟡 NEUTRAL"

    if score >= 40:

        return "🟠 BEARISH"

    return "🔴 VERY BEARISH"