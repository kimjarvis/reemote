from abc import ABC, abstractmethod
from typing import AsyncGenerator

from reemote.context import Context, ConnectionType
from reemote.core.response import ResponseElement

from pathlib import PurePath
from typing import Optional, Union
from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field, field_validator


class CommonCallbackRequestModel(BaseModel):
    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    group: Optional[str] = Field(
        default="all", description="The inventory host group. Defaults to 'all'."
    )
    name: Optional[str] = Field(default=None, description="Optional name.")


def common_callback_request(
    group: Optional[str] = Query(
        "all", description="Optional inventory group (defaults to 'all')"
    ),
    name: Optional[str] = Query(None, description="Optional name"),
) -> CommonCallbackRequestModel:
    """FastAPI dependency for common parameters"""
    return CommonCallbackRequestModel(group=group, name=name)


class AbstractCallbackRequest(BaseModel):
    """Abstract class for local commands"""
    dummy: bool = True

class Callback(ABC):
    Model = AbstractCallbackRequest

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

        # Check if the subclass overrides the 'Model' field
        if cls.Model is Callback.Model:  # If it's still the same as the base class
            raise NotImplementedError(
                f"Class {cls.__name__} must override the 'Model' class field."
            )

        cls.child = cls.__name__  # Set the 'child' field to the name of the subclass

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def execute(self) -> AsyncGenerator[Context, ResponseElement]:
        model_instance = self.Model.model_validate(self.kwargs)
        yield Context(
            type=ConnectionType.LOCAL,
            callback=self.callback,
            call=self.__class__.child + "(" + str(model_instance) + ")",
            caller=model_instance,
            group=model_instance.group,
        )

    @staticmethod
    @abstractmethod
    async def callback(context: Context) -> None:
        pass