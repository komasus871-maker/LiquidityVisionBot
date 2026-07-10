class NewsEngine:

    HIGH = "High"

    MEDIUM = "Medium"

    LOW = "Low"

    def analyze(

        self,

        events

    ):

        if not events:

            return {

                "allow": True,

                "impact": self.LOW,

                "message": "No major news"

            }

        highest = max(

            events,

            key=lambda x: x["impact"]

        )

        if highest["impact"] == self.HIGH:

            return {

                "allow": False,

                "impact": self.HIGH,

                "message": highest["title"]

            }

        if highest["impact"] == self.MEDIUM:

            return {

                "allow": True,

                "impact": self.MEDIUM,

                "message": highest["title"]

            }

        return {

            "allow": True,

            "impact": self.LOW,

            "message": "Low impact news"

        }