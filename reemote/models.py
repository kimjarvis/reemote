from pathlib import PurePath
from typing import Optional, Union

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field, field_validator


class CommonModel(BaseModel):
    """Common parameters shared across command types"""

    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    group: Optional[str] = Field(default="all", description="Optional inventory group")
    name: Optional[str] = Field(default=None, description="Optional name")
    sudo: bool = Field(default=False, description="Whether to use sudo")
    su: bool = Field(default=False, description="Whether to use su")
    get_pty: bool = Field(default=False, description="Whether to get a PTY")

    def __repr__(self) -> str:
        """Use detailed_repr for representation"""
        return self.detailed_repr()

    def __str__(self) -> str:
        return self.__repr__()

    def detailed_repr(self) -> str:
        """Show all fields including defaults"""
        field_strings = []

        # Always include all fields in detailed representation
        if self.group is not None:
            field_strings.append(f"group={self.group!r}")
        if self.name is not None:
            field_strings.append(f"name={self.name!r}")
        field_strings.append(f"sudo={self.sudo!r}")
        field_strings.append(f"su={self.su!r}")
        field_strings.append(f"get_pty={self.get_pty!r}")

        return f"CommonParams({', '.join(field_strings)})"


def commonmodel(
    group: Optional[str] = Query(
        "all", description="Optional inventory group (defaults to 'all')"
    ),
    name: Optional[str] = Query(None, description="Optional name"),
    sudo: bool = Query(False, description="Whether to use sudo"),
    su: bool = Query(False, description="Whether to use su"),
    get_pty: bool = Query(False, description="Whether to get a PTY"),
) -> CommonModel:
    """FastAPI dependency for common parameters"""
    return CommonModel(group=group, name=name, sudo=sudo, su=su, get_pty=get_pty)


class LocalModel(BaseModel):
    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    group: Optional[str] = "all"
    name: Optional[str] = None


def localmodel(
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


class RemoteModel(BaseModel):
    """Common parameters shared across command types"""
    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    group: Optional[str] = "all"
    name: Optional[str] = None
    sudo: bool = False
    su: bool = False
    get_pty: bool = False

    def __repr__(self) -> str:
        """Use detailed_repr for representation"""
        return self.detailed_repr()

    def __str__(self) -> str:
        return self.__repr__()

    def detailed_repr(self) -> str:
        """Generate a detailed representation dynamically for any derived class."""
        # Get all fields as a dictionary
        fields_dict = self.model_dump()

        # Format fields for display
        field_reprs = [f"{name}={value!r}" for name, value in fields_dict.items()]

        # Get class name and remove "Model" suffix if present
        class_name = self.__class__.__name__
        if class_name.endswith("Model"):
            class_name = class_name[:-5]  # Remove "Model" (5 characters)

        return f"{class_name}({', '.join(field_reprs)})"


def remotemodel(
    group: Optional[str] = Query(
        "all", description="Optional inventory group (defaults to 'all')"
    ),
    name: Optional[str] = Query(None, description="Optional name"),
    sudo: bool = Query(False, description="Whether to use sudo"),
    su: bool = Query(False, description="Whether to use su"),
    get_pty: bool = Query(False, description="Whether to get a PTY"),
) -> RemoteModel:
    """FastAPI dependency for common parameters"""
    return RemoteModel(group=group, name=name, sudo=sudo, su=su, get_pty=get_pty)


