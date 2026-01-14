from abc import ABC, abstractmethod
from typing import Optional

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field


class CommonOperationRequestModel(BaseModel):
    """Common parameters shared across command types"""

    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    group: Optional[str] = Field(
        default="all", description="Optional inventory group (defaults to 'all')."
    )
    name: Optional[str] = Field(default=None, description="Optional name.")
    sudo: bool = Field(default=False, description="Execute command with sudo.")
    su: bool = Field(default=False, description="Execute command with su.")


def common_operation_request(
    group: Optional[str] = Query(
        "all", description="Optional inventory group (defaults to 'all')"
    ),
    name: Optional[str] = Query(None, description="Optional name"),
    sudo: bool = Query(False, description="Whether to use sudo"),
    su: bool = Query(False, description="Whether to use su"),
) -> CommonOperationRequestModel:
    """FastAPI dependency for common parameters"""
    return CommonOperationRequestModel(group=group, name=name, sudo=sudo, su=su)


class AbstractOperation(BaseModel):
    """Abstract class for local commands"""

    dummy: bool = True


class Operation(ABC):
    request_model = AbstractOperation


    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

        # Check if the subclass overrides the 'Model' field
        if (
            cls.request_model is Operation.request_model
        ):  # If it's still the same as the base class
            raise NotImplementedError(
                f"Class {cls.__name__} must override the 'Model' class field."
            )

        cls.child = cls.__name__  # Set the 'child' field to the name of the subclass

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        # Define the fields that are considered "common" based on RemoteParams
        common_fields = set(CommonOperationRequestModel.model_fields.keys())

        # Separate kwargs into common_kwargs and extra_kwargs
        self.common_kwargs = {
            key: value for key, value in kwargs.items() if key in common_fields
        }
        self.extra_kwargs = {
            key: value for key, value in kwargs.items() if key not in common_fields
        }

    @abstractmethod
    async def execute(
        self,
    ):  # -> AsyncGenerator[Context, ResponseElement]: # Avoid circular import
        pass
