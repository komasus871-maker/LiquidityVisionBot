class EntryEngine:

    def evaluate(

        self,

        trend,

        structure,

        zone_quality,

        confidence,

        probability

    ):

        if confidence < 60:

            return False

        if probability < 60:

            return False

        if zone_quality < 55:

            return False

        if "Range" in structure:

            return False

        return True