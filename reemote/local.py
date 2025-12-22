from typing import AsyncGenerator

from reemote.command import Command, ConnectionType
from reemote.response import Response


class Local:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def execute(self) -> AsyncGenerator[Command, Response]:
        model_instance = self.Model(**self.kwargs)

        yield Command(
            type=ConnectionType.LOCAL,
            callback=self._callback,
            call=str(model_instance),
            caller=model_instance,
            group=model_instance.group,
        )
