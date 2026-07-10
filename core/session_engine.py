from datetime import datetime, timezone


class SessionEngine:

    ASIA = "Asia"

    LONDON = "London"

    NEW_YORK = "New York"

    OVERLAP = "London + New York"

    CLOSED = "Closed"

    def analyze(self):

        hour = datetime.now(

            timezone.utc

        ).hour

        if 0 <= hour < 7:

            return {

                "session": self.ASIA,

                "score": 55,

                "volatility": "Low"

            }

        if 7 <= hour < 13:

            return {

                "session": self.LONDON,

                "score": 85,

                "volatility": "High"

            }

        if 13 <= hour < 16:

            return {

                "session": self.OVERLAP,

                "score": 100,

                "volatility": "Very High"

            }

        if 16 <= hour < 21:

            return {

                "session": self.NEW_YORK,

                "score": 80,

                "volatility": "High"

            }

        return {

            "session": self.CLOSED,

            "score": 25,

            "volatility": "Low"

        }