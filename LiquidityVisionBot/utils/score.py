def signal(score):

    if score >= 80:
        return "🟢 Strong Long"

    if score >= 60:
        return "🟡 Long"

    if score >= 40:
        return "⚪ Neutral"

    if score >= 20:
        return "🟠 Short"

    return "🔴 Strong Short"