from pathlib import PurePath
from typing import Union

from fastapi import APIRouter
from pydantic import (
    Field,
    field_validator,
)
from reemote.callback import CommonCallbackRequestModel

router = APIRouter()


class PathRequestModel(CommonCallbackRequestModel):
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
