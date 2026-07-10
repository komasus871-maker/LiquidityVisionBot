from abc import ABC
from abc import abstractmethod


class Stage(ABC):

    @abstractmethod

    def process(

        self,

        context

    ):

        pass