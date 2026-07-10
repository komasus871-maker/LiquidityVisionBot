class Engine:

    def __init__(

        self,

        stages

    ):

        self.stages = stages

    def run(

        self,

        context

    ):

        for stage in self.stages:

            stage.process(

                context

            )

        return context