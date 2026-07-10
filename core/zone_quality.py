class ZoneQuality:

    def score(

        self,

        touches,

        age,

        size,

        displacement,

        volume

    ):

        score = 0

        if touches == 0:

            score += 25

        elif touches == 1:

            score += 15

        elif touches == 2:

            score += 8

        if age <= 20:

            score += 20

        elif age <= 50:

            score += 10

        if displacement >= 70:

            score += 25

        elif displacement >= 50:

            score += 15

        if volume >= 1.5:

            score += 20

        elif volume >= 1.0:

            score += 10

        if size <= 0.3:

            score += 10

        elif size <= 0.6:

            score += 5

        return min(score, 100)