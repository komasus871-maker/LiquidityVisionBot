from core.database import Database


class HistoryEngine:

    def __init__(self):

        self.db = Database()

    def check(

        self,

        signal,

        current_price

    ):

        result = None

        rr = 0

        if signal["recommendation"] in (

            "🟢 BUY",

            "🔥 STRONG BUY"

        ):

            if current_price <= signal["stop"]:

                result = "SL"

                rr = -1

            elif current_price >= signal["tp3"]:

                result = "TP3"

                rr = signal["rr"]

            elif current_price >= signal["tp2"]:

                result = "TP2"

                rr = signal["rr"] * 0.66

            elif current_price >= signal["tp1"]:

                result = "TP1"

                rr = signal["rr"] * 0.33

        else:

            if current_price >= signal["stop"]:

                result = "SL"

                rr = -1

            elif current_price <= signal["tp3"]:

                result = "TP3"

                rr = signal["rr"]

            elif current_price <= signal["tp2"]:

                result = "TP2"

                rr = signal["rr"] * 0.66

            elif current_price <= signal["tp1"]:

                result = "TP1"

                rr = signal["rr"] * 0.33

        return result, rr