from typing import AsyncGenerator

from reemote.command import Command, ConnectionType
from reemote.response import Response


class Local:
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        cls.child = cls.__name__  # Set the 'child' field to the name of the subclass

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def execute(self) -> AsyncGenerator[Command, Response]:
        model_instance = self.Model.model_validate(self.kwargs)

        yield Command(
            type=ConnectionType.LOCAL,
            callback=self._callback,
            call=self.__class__.child+"("+str(model_instance)+")",
            caller=model_instance,
            group=model_instance.group,
        )
