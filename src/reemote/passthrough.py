from abc import ABC, abstractmethod
from typing import AsyncGenerator, Optional

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field

from reemote.context import ContextType, Context
from reemote.response import AbstractResponseModel


class CommonPassthroughRequest(BaseModel):
    model_config = {
        "validate_assignment": True,
        "extra": "forbid",
    }
    group: Optional[str] = None
    name: Optional[str] = None

def common_passthrough_request(
    group: Optional[str] = Query(
        "all", description="Optional inventory group (defaults to 'all')", examples=["'server104'"]
    ),
    name: Optional[str] = Query(None, description="Optional name", examples=["'A descriptive name'"]),
) -> CommonPassthroughRequest:
    """FastAPI dependency for common parameters"""
    return CommonPassthroughRequest(group=group, name=name)


class AbstractPassthrough(BaseModel):
    """Abstract class for local commands"""

    dummy: bool = True


class Passthrough(ABC):
    request_model = AbstractPassthrough
    response_model = AbstractPassthrough

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

        # # Check if the subclass overrides the 'Model' field
        # if (
        #     cls.request_model is Passthrough.request_model
        # ):  # If it's still the same as the base class
        #     raise NotImplementedError(
        #         f"Class {cls.__name__} must override the 'Model' class field."
        #     )

        cls.child = cls.__name__  # Set the 'child' field to the name of the subclass

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    @staticmethod
    @abstractmethod
    async def execute(self) -> AsyncGenerator[Context, AbstractResponseModel]:
        pass
