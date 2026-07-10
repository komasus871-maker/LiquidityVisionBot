class MarketPhase:

    ACCUMULATION = "Accumulation"

    DISTRIBUTION = "Distribution"

    EXPANSION = "Expansion"

    PULLBACK = "Pullback"

    CONSOLIDATION = "Consolidation"

    REVERSAL = "Reversal"

    UNKNOWN = "Unknown"

    def detect(

        self,

        trend,

        structure,

        bos,

        choch,

        premium,

        displacement,

        volume

    ):

        if (

            "Bullish" in trend

            and

            "Bullish" in structure

            and

            "Bullish" in bos

        ):

            return self.EXPANSION

        if (

            "Bearish" in trend

            and

            "Bearish" in structure

            and

            "Bearish" in bos

        ):

            return self.EXPANSION

        if (

            "Bullish" in choch

            or

            "Bearish" in choch

        ):

            return self.REVERSAL

        if (

            "Range" in structure

        ):

            return self.CONSOLIDATION

        if (

            "Discount" in premium["zone"]

            and

            "Bearish" in displacement

        ):

            return self.ACCUMULATION

        if (

            "Premium" in premium["zone"]

            and

            "Bullish" in displacement

        ):

            return self.DISTRIBUTION

        return self.UNKNOWN