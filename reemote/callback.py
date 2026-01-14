from abc import ABC, abstractmethod
from typing import AsyncGenerator, Optional

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field

from reemote.context import ConnectionType, Context
from reemote.core.response import AbstractResponseModel


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


class AbstractCallback(BaseModel):
    """Abstract class for local commands"""

    dummy: bool = True


class Callback(ABC):
    request_model = AbstractCallback

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

        # Check if the subclass overrides the 'Model' field
        if (
            cls.request_model is Callback.request_model
        ):  # If it's still the same as the base class
            raise NotImplementedError(
                f"Class {cls.__name__} must override the 'Model' class field."
            )

        cls.child = cls.__name__  # Set the 'child' field to the name of the subclass

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def execute(self) -> AsyncGenerator[Context, AbstractResponseModel]:
        model_instance = self.request_model.model_validate(self.kwargs)
        yield Context(
            type=ConnectionType.CALLBACK,
            callback=self.callback,
            call=self.__class__.child + "(" + str(model_instance) + ")",
            caller=model_instance,
            group=model_instance.group,
        )

    @staticmethod
    @abstractmethod
    async def callback(context: Context) -> None:
        pass
