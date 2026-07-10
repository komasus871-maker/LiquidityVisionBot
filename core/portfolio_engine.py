class PortfolioEngine:

    def rank(

        self,

        setups

    ):

        setups.sort(

            key=lambda x: (

                x["probability"],

                x["quality"],

                x["pattern"]["winrate"]

            ),

            reverse=True

        )

        return setups