class Recommendation:

    @staticmethod
    def get(score):

        if score >= 95:

            return "🔥 STRONG LONG"

        if score >= 80:

            return "🟢 LONG"

        if score >= 65:

            return "🟡 WAIT"

        if score >= 40:

            return "🟠 SHORT"

        return "🔴 STRONG SHORT"