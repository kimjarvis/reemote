from abc import abstractmethod
from typing import AsyncGenerator
from reemote.core.models import RemoteModel
from reemote.context import Context


class Remote:
    Model = RemoteModel

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

        # Check if the subclass overrides the 'Model' field
        if cls.Model is Remote.Model:  # If it's still the same as the base class
            raise NotImplementedError(f"Class {cls.__name__} must override the 'Model' class field.")

        cls.child = cls.__name__  # Set the 'child' field to the name of the subclass

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        # Define the fields that are considered "common" based on RemoteParams
        common_fields = set(RemoteModel.model_fields.keys())

        # Separate kwargs into common_kwargs and extra_kwargs
        self.common_kwargs = {key: value for key, value in kwargs.items() if key in common_fields}
        self.extra_kwargs = {key: value for key, value in kwargs.items() if key not in common_fields}

    @abstractmethod
    async def execute(self) -> AsyncGenerator["Context", "Response"]:
        pass