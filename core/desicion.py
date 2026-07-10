from core.models import Decision
from core.models import Score
from core.models import Context


class DecisionEngine:

    def decide(

        self,

        score: Score,

        context: Context

    ):

        confidence = (

            abs(score.difference) * 0.45 +

            context.alignment * 0.25 +

            context.quality * 0.15 +

            context.pattern_winrate * 0.10 +

            context.session_score * 0.05

        )

        confidence = min(

            100,

            round(confidence, 2)

        )

        if score.difference >= 30:

            recommendation = "🔥 STRONG BUY"

        elif score.difference >= 15:

            recommendation = "🟢 BUY"

        elif score.difference <= -30:

            recommendation = "🔴 STRONG SELL"

        elif score.difference <= -15:

            recommendation = "🟠 SELL"

        else:

            recommendation = "🟡 WAIT"

        if confidence >= 90:

            quality = "⭐⭐⭐⭐⭐"

        elif confidence >= 75:

            quality = "⭐⭐⭐⭐"

        elif confidence >= 55:

            quality = "⭐⭐⭐"

        elif confidence >= 35:

            quality = "⭐⭐"

        else:

            quality = "⭐"

        return Decision(

            recommendation=recommendation,

            bullish=score.difference > 0,

            confidence=confidence,

            bull_score=score.bull,

            bear_score=score.bear,

            context_score=context.alignment,

            setup_score=max(

                score.bull,

                score.bear

            ),

            quality=quality

        )