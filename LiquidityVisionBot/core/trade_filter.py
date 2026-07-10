class TradeFilter:

    def allow(

        self,

        execution,

        confirmation,

        entry

    ):

        if not execution["execute"]:

            return False

        if not confirmation["passed"]:

            return False

        if not entry:

            return False

        return True