from core.database import Database


class StatisticsEngine:

    def __init__(self):

        self.db = Database()

    def build(self):

        data = self.db.fetch_all()

        total = len(data)

        wins = 0

        losses = 0

        total_rr = 0

        for row in data:

            result = row[-2]

            rr = row[-1] or 0

            if result in (

                "TP1",

                "TP2",

                "TP3"

            ):

                wins += 1

            elif result == "SL":

                losses += 1

            total_rr += rr

        winrate = (

            wins / total * 100

            if total else 0

        )

        avg_rr = (

            total_rr / total

            if total else 0

        )

        return {

            "signals": total,

            "wins": wins,

            "losses": losses,

            "winrate": round(

                winrate,

                2

            ),

            "average_rr": round(

                avg_rr,

                2

            )

        }