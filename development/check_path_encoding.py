from pathlib import PurePath
from typing import Union

from pydantic import BaseModel, Field, field_validator


class PathRequest(BaseModel):
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


# Test the model
if __name__ == "__main__":
    # Create an instance of PathRequest
    request = PathRequest(path="/home/user")

    # Print the model dump (serialized output)
    print("Serialized Output:", request.model_dump())

    # Verify deserialization and validation
    try:
        invalid_request = PathRequest(path=None)  # This should raise a ValueError
    except ValueError as e:
        print("Validation Error:", e)

    # Test with a valid bytes input
    valid_bytes_request = PathRequest(path=b"/home/user")
    print("Bytes Input Serialized:", valid_bytes_request.model_dump())