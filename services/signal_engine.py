from core.models import Signal


class SignalEngine:

    def build(

        self,

        market

    ):

        signals = []

        signals.append(

            Signal(

                name="Trend",

                direction="bull" if "Bullish" in market.trend else "bear",

                strength=100,

                confidence=85,

                active=True,

                weight=10,

                reason="Trend confirmation"

            )

        )

        signals.append(

            Signal(

                name="Structure",

                direction="bull" if "Bullish" in market.structure else "bear",

                strength=100,

                confidence=90,

                active=True,

                weight=10,

                reason="Market structure"

            )

        )

        signals.append(

            Signal(

                name="BOS",

                direction="bull" if "Bullish" in market.bos else "bear",

                strength=95,

                confidence=90,

                active=True,

                weight=10,

                reason="Break Of Structure"

            )

        )

        signals.append(

            Signal(

                name="CHOCH",

                direction="bull" if "Bullish" in market.choch else "bear",

                strength=90,

                confidence=85,

                active=True,

                weight=10,

                reason="Change Of Character"

            )

        )

        signals.append(

            Signal(

                name="Sweep",

                direction="bull" if "Sell Side Sweep" in market.sweep else "bear",

                strength=85,

                confidence=80,

                active=True,

                weight=8,

                reason="Liquidity sweep"

            )

        )

        signals.append(

            Signal(

                name="Order Block",

                direction="bull" if "Bullish" in market.order_block else "bear",

                strength=90,

                confidence=90,

                active=True,

                weight=10,

                reason="Order Block"

            )

        )

        signals.append(

            Signal(

                name="Breaker",

                direction="bull" if "Bullish" in market.breaker else "bear",

                strength=80,

                confidence=80,

                active=True,

                weight=6,

                reason="Breaker Block"

            )

        )

        signals.append(

            Signal(

                name="Mitigation",

                direction="bull" if "Bullish" in market.mitigation else "bear",

                strength=80,

                confidence=80,

                active=True,

                weight=6,

                reason="Mitigation Block"

            )

        )

        signals.append(

            Signal(

                name="FVG",

                direction="bull" if "Bullish" in market.fvg else "bear",

                strength=90,

                confidence=90,

                active=True,

                weight=10,

                reason="Fair Value Gap"

            )

        )

        signals.append(

            Signal(

                name="Premium",

                direction="bull" if "Discount" in market.premium["zone"] else "bear",

                strength=85,

                confidence=85,

                active=True,

                weight=8,

                reason="Premium / Discount"

            )

        )

        signals.append(

            Signal(

                name="Volume",

                direction="bull" if "Spike" in market.volume else "bear",

                strength=75,

                confidence=75,

                active=True,

                weight=5,

                reason="Volume"

            )

        )

        signals.append(

            Signal(

                name="Displacement",

                direction="bull" if "Bullish" in market.displacement else "bear",

                strength=80,

                confidence=80,

                active=True,

                weight=8,

                reason="Displacement"

            )

        )

        signals.append(

            Signal(

                name="Momentum",

                direction="bull" if (
                    market.macd == "🟢 Bullish"
                    and
                    45 <= market.rsi <= 65
                ) else "bear",

                strength=85,

                confidence=85,

                active=True,

                weight=8,

                reason="Momentum"

            )

        )

        return signals