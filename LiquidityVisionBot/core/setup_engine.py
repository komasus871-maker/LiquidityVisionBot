class SetupEngine:

    def classify(

        self,

        probability,

        confidence,

        quality

    ):

        total = (

            probability +

            confidence +

            quality

        ) / 3

        if total >= 90:

            return "A+"

        if total >= 80:

            return "A"

        if total >= 70:

            return "B"

        if total >= 60:

            return "C"

        return "D"