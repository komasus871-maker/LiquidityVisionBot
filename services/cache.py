import time


class Cache:

    def __init__(self):

        self.cache = {}

    def get(self, key):

        if key not in self.cache:

            return None

        value, expires = self.cache[key]

        if time.time() > expires:

            del self.cache[key]

            return None

        return value

    def set(

        self,

        key,

        value,

        ttl=20

    ):

        self.cache[key] = (

            value,

            time.time() + ttl

        )


cache = Cache()