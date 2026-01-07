from abc import abstractmethod
from typing import AsyncGenerator

from reemote.context import Context, ConnectionType
from reemote.core.response import Response

from pathlib import PurePath
from typing import Optional, Union
from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field, field_validator


class LocalModel(BaseModel):
    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    group: Optional[str] = Field(
        default="all", description="The inventory host group. Defaults to 'all'."
    )
    name: Optional[str] = Field(default=None, description="Optional name.")


def localmodel(
    group: Optional[str] = Query(
        "all", description="Optional inventory group (defaults to 'all')"
    ),
    name: Optional[str] = Query(None, description="Optional name"),
) -> LocalModel:
    """FastAPI dependency for common parameters"""
    return LocalModel(group=group, name=name)


class LocalPathModel(LocalModel):
    path: Union[PurePath, str, bytes] = Field(..., examples=["/home/user", "testdata"])

    @field_validator("path", mode="before")
    @classmethod
    def ensure_path_is_purepath(cls, v):
        if v is None:
            raise ValueError("path cannot be None.")
        if not isinstance(v, PurePath):
            try:
                return PurePath(v)
            except TypeError:
                raise ValueError(f"Cannot convert {v} to PurePath.")
        return v


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