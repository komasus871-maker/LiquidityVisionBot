from services.signal_engine import SignalEngine


class SignalStage:

    def process(

        self,

        context

    ):

        engine = SignalEngine()

        signals = engine.build(

            context["analysis"]

        )

        context["signals"] = signals

        return context