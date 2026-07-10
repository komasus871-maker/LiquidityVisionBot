import re


class Ranking:

    @staticmethod
    def score(text):

        match = re.search(

            r"Score:\s*(\d+)",

            text

        )

        if not match:

            return 0

        return int(

            match.group(1)

        )

    @staticmethod
    def sort(results):

        return sorted(

            results,

            key=lambda x: Ranking.score(

                x[1]

            ),

            reverse=True

        )