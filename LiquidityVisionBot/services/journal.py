class Journal:

    def statistics(self, trades):

        wins = 0

        losses = 0

        for trade in trades:

            if trade["profit"] > 0:

                wins += 1

            else:

                losses += 1

        total = wins + losses

        if total == 0:

            winrate = 0

        else:

            winrate = round(

                wins / total * 100,

                2

            )

        return {

            "wins": wins,

            "losses": losses,

            "winrate": winrate

        }