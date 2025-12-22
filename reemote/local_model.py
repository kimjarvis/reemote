from typing import Optional
from typing import AsyncGenerator, Union
from pydantic import BaseModel, ConfigDict
from reemote.command import Command, ConnectionType
from reemote.response import Response

from pathlib import PurePath

from fastapi import Query
from pydantic import Field, field_validator

class LocalModel(BaseModel):
    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    group: Optional[str] = "all"
    name: Optional[str] = None

def local_params(
    group: Optional[str] = Query(
        "all", description="Optional inventory group (defaults to 'all')"
    ),
    name: Optional[str] = Query(None, description="Optional name"),
) -> LocalModel:
    """FastAPI dependency for common parameters"""
    return LocalModel(group=group, name=name)


class LocalPathModel(LocalModel):
    path: Union[PurePath, str, bytes] = Field(
        ...,  # Required field
    )

    @field_validator('path', mode='before')
    @classmethod
    def ensure_path_is_purepath(cls, v):
        """
        Ensure the 'path' field is converted to a PurePath object.
        This runs before the field is validated by Pydantic.
        """
        if v is None:
            raise ValueError("path cannot be None.")
        if not isinstance(v, PurePath):
            try:
                return PurePath(v)
            except TypeError:
                raise ValueError(f"Cannot convert {v} to PurePath.")
        return v


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
