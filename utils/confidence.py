class Confidence:

    @staticmethod
    def get(score):

        if score > 100:

            score = 100

        if score < 0:

            score = 0

        return score