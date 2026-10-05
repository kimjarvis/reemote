from pathlib import PurePath
from typing import Union

from fastapi import APIRouter
from pydantic import (
    BaseModel,
    Field,
    field_validator,
)
from reemote.callback import CommonCallbackRequest

router = APIRouter()


class PathRequest(CommonCallbackRequest):
    path: Union[PurePath, str, bytes] = Field(..., examples=["/home/user", "testdata"])

    @field_validator("path", mode="before")
    @classmethod
    def ensure_path_is_purepath(cls, v):
        if v is None:
            raise ValueError("path cannot be None.")
        if isinstance(v, bytes):
            try:
                v = v.decode("utf-8")  # Decode bytes to string
            except UnicodeDecodeError:
                raise ValueError(f"Cannot decode bytes to string: {v}")
        if not isinstance(v, PurePath):
            try:
                return PurePath(v)
            except TypeError:
                raise ValueError(f"Cannot convert {v} to PurePath.")
        return v

    # Custom serialization in Pydantic v2
    def model_dump(self, **kwargs):
        data = super().model_dump(**kwargs)
        data["path"] = str(data["path"])  # Ensure `path` is serialized as a string
        return data
