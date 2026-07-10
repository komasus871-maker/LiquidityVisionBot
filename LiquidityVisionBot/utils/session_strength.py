from datetime import datetime


class SessionStrength:

    @staticmethod
    def current():

        hour = datetime.utcnow().hour

        if 12 <= hour <= 16:

            return "🇺🇸 London / New York"

        if 7 <= hour <= 11:

            return "🇬🇧 London"

        if 0 <= hour <= 6:

            return "🇯🇵 Asia"

        return "🌍 Low Activity"