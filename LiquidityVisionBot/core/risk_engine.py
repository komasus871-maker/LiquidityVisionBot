class RiskEngine:

    def lot(

        self,

        balance,

        risk,

        stop

    ):

        if stop <= 0:

            return 0

        return round(

            (

                balance *

                risk

            ) /

            stop,

            4

        )

    def risk_level(

        self,

        confidence

    ):

        if confidence >= 90:

            return 0.02

        if confidence >= 75:

            return 0.015

        if confidence >= 60:

            return 0.01

        return 0.005