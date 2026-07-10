from collections import defaultdict


class PatternMemory:

    def __init__(self):

        self.patterns = defaultdict(

            lambda: {

                "wins": 0,

                "losses": 0,

                "tp1": 0,

                "tp2": 0,

                "tp3": 0,

                "total": 0,

                "avg_rr": 0,

            }

        )

    def _key(

        self,

        analysis

    ):

        return (

            analysis["trend"],

            analysis["structure"],

            analysis["bos"],

            analysis["choch"],

            analysis["breaker"],

            analysis["mitigation"],

            analysis["fvg"],

            analysis["premium"]["zone"],

            analysis["volume"],

            analysis["displacement"],

        )

    def register(

        self,

        analysis,

        result,

        rr

    ):

        key = self._key(

            analysis

        )

        item = self.patterns[key]

        item["total"] += 1

        if result == "SL":

            item["losses"] += 1

        elif result == "TP1":

            item["wins"] += 1

            item["tp1"] += 1

        elif result == "TP2":

            item["wins"] += 1

            item["tp2"] += 1

        elif result == "TP3":

            item["wins"] += 1

            item["tp3"] += 1

        item["avg_rr"] = (

            (

                item["avg_rr"]

                *

                (

                    item["total"] - 1

                )

            )

            +

            rr

        ) / item["total"]

    def statistics(

        self,

        analysis

    ):

        key = self._key(

            analysis

        )

        item = self.patterns[key]

        if item["total"] == 0:

            return {

                "samples": 0,

                "win_rate": 0,

                "avg_rr": 0,

            }

        return {

            "samples": item["total"],

            "win_rate": round(

                item["wins"]

                /

                item["total"]

                *

                100,

                2

            ),

            "avg_rr": round(

                item["avg_rr"],

                2

            ),

            "tp1": item["tp1"],

            "tp2": item["tp2"],

            "tp3": item["tp3"],

            "losses": item["losses"],

        }