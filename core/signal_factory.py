from core.signal import Signal
from core.weights import WEIGHTS


class SignalFactory:

    def build(

        self,

        trend,

        structure,

        bos,

        choch,

        order_block,

        breaker,

        mitigation,

        fvg,

        displacement,

        sweep,

    ):

        signals = []

        def add(

            name,

            text

        ):

            if "Bullish" in text:

                signals.append(

                    Signal(

                        name,

                        "bull",

                        WEIGHTS[name],

                        f"✅ Bull {name.title()}"

                    )

                )

            elif "Bearish" in text:

                signals.append(

                    Signal(

                        name,

                        "bear",

                        WEIGHTS[name],

                        f"✅ Bear {name.title()}"

                    )

                )

        add("trend", trend)

        add("structure", structure)

        add("bos", bos)

        add("choch", choch)

        add("order_block", order_block)

        add("breaker", breaker)

        add("mitigation", mitigation)

        add("fvg", fvg)

        add("displacement", displacement)

        if "Sell Side Sweep" in sweep:

            signals.append(

                Signal(

                    "sweep",

                    "bull",

                    WEIGHTS["sweep"],

                    "✅ Sell Side Sweep"

                )

            )

        elif "Buy Side Sweep" in sweep:

            signals.append(

                Signal(

                    "sweep",

                    "bear",

                    WEIGHTS["sweep"],

                    "✅ Buy Side Sweep"

                )

            )

        return signals