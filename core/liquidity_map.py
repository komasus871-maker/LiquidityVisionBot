class LiquidityMap:

    def build(

        self,

        equal_highs,

        equal_lows,

        highs,

        lows,

        current

    ):

        zones = []

        for level in equal_highs:

            zones.append({

                "price": level,

                "type": "Buy Liquidity",

                "distance": abs(

                    current - level

                )

            })

        for level in equal_lows:

            zones.append({

                "price": level,

                "type": "Sell Liquidity",

                "distance": abs(

                    current - level

                )

            })

        zones.sort(

            key=lambda x: x["distance"]

        )

        return zones