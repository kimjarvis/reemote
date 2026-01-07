from abc import ABC, abstractmethod
from typing import AsyncGenerator, Any

from reemote.core.context import Context, ConnectionType
from reemote.core.response import Response
from reemote.core.models import LocalModel


class Local:
    Model = LocalModel

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

        # Check if the subclass overrides the 'Model' field
        if cls.Model is Local.Model:  # If it's still the same as the base class
            raise NotImplementedError(
                f"Class {cls.__name__} must override the 'Model' class field."
            )

        cls.child = cls.__name__  # Set the 'child' field to the name of the subclass

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def execute(self) -> AsyncGenerator[Context, Response]:
        model_instance = self.Model.model_validate(self.kwargs)
        yield Context(
            type=ConnectionType.LOCAL,
            callback=self._callback,
            call=self.__class__.child + "(" + str(model_instance) + ")",
            caller=model_instance,
            group=model_instance.group,
        )

    @staticmethod
    @abstractmethod
    async def _callback(context: Context) -> None:
        pass