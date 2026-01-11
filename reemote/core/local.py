from abc import abstractmethod
from typing import AsyncGenerator

from reemote.context import Context, ConnectionType
from reemote.core.response import ResponseElement

from pathlib import PurePath
from typing import Optional, Union
from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field, field_validator


class LocalRequestModel(BaseModel):
    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    group: Optional[str] = Field(
        default="all", description="The inventory host group. Defaults to 'all'."
    )
    name: Optional[str] = Field(default=None, description="Optional name.")


def localrequestmodel(
    group: Optional[str] = Query(
        "all", description="Optional inventory group (defaults to 'all')"
    ),
    name: Optional[str] = Query(None, description="Optional name"),
) -> LocalRequestModel:
    """FastAPI dependency for common parameters"""
    return LocalRequestModel(group=group, name=name)




class Local:
    Model = LocalRequestModel

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

        cls.child = cls.__name__  # Set the 'child' field to the name of the subclass

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def execute(self) -> AsyncGenerator[Context, ResponseElement]:
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