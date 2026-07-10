from utils.logger import logger


class Notifier:

    async def signal(

        self,

        coin

    ):

        logger.info(

            f"""

NEW SIGNAL

{coin["symbol"]}

Score

{coin["score"]}

"""

        )