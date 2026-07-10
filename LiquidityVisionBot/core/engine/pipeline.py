class Pipeline:

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

            context = stage.process(

                context

            )

        return context