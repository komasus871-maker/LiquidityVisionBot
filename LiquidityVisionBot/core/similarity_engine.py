class SimilarityEngine:

    def similarity(

        self,

        current,

        history

    ):

        score = 0

        total = 0

        keys = (

            "trend",

            "structure",

            "bos",

            "choch",

            "breaker",

            "mitigation",

            "fvg",

            "premium",

            "volume",

            "displacement",

        )

        for key in keys:

            total += 1

            if current[key] == history[key]:

                score += 1

        return round(

            score /

            total *

            100,

            2

        )

    def find(

        self,

        current,

        history

    ):

        matches = []

        for signal in history:

            similarity = self.similarity(

                current,

                signal

            )

            if similarity >= 70:

                matches.append({

                    "similarity": similarity,

                    "signal": signal

                })

        matches.sort(

            key=lambda x: x["similarity"],

            reverse=True

        )

        return matches