from abc import ABC, abstractmethod
from typing import AsyncGenerator, Optional

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field

from reemote.context import ContextType, Context
from reemote.response import AbstractResponseModel


class CommonCallbackRequest(BaseModel):
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
) -> CommonCallbackRequest:
    """FastAPI dependency for common parameters"""
    return CommonCallbackRequest(group=group, name=name)


from abc import ABC, abstractmethod
from typing import AsyncGenerator

class AbstractCallback(BaseModel):
    """Abstract class for local commands"""

    dummy: bool = True


class Callback(ABC):
    request_schema = None  # Placeholder for subclasses to override
    response_schema = None  # Placeholder for subclasses to override

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

        # # Ensure 'request_model' is defined and is a callable (class)
        # if cls.request_model is None or not callable(cls.request_model):
        #     raise TypeError(
        #         f"Class {cls.__name__} must define 'request_model' as a callable (class)."
        #     )
        #
        # # Ensure 'response_model' is defined and is a callable (class)
        # if cls.response_model is None or not callable(cls.response_model):
        #     raise TypeError(
        #         f"Class {cls.__name__} must define 'response_model' as a callable (class)."
        #     )
        #
        # # Ensure 'callback' method is implemented in the child class
        # if "callback" not in cls.__dict__ or not callable(cls.callback):
        #     raise NotImplementedError(
        #         f"Class {cls.__name__} must implement the 'callback' method."
        #     )
        #
        # # Ensure 'execute' method is NOT overridden in the child class
        # if "execute" in cls.__dict__:
        #     raise RuntimeError(
        #         f"Class {cls.__name__} must not override the 'execute' method."
        #     )

        cls.child = cls.__name__  # Set the 'child' field to the name of the subclass

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def execute(self) -> AsyncGenerator[Context, AbstractResponseModel]:
        model_instance = self.request_schema.model_validate(self.kwargs)
        result = yield Context(
            type=ContextType.CALLBACK,
            callback=self.callback,
            call=self.__class__.child + "(" + str(model_instance) + ")",
            caller=model_instance,
            group=model_instance.group,
        )
        self.response_schema.model_validate(result)

    @staticmethod
    @abstractmethod
    async def callback(context: Context) -> None:
        pass