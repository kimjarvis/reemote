from typing import Optional

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field


class CommonParams(BaseModel):
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


def common_params(
    group: Optional[str] = Query(
        "all", description="Optional inventory group (defaults to 'all')"
    ),
    name: Optional[str] = Query(None, description="Optional name"),
    sudo: bool = Query(False, description="Whether to use sudo"),
    su: bool = Query(False, description="Whether to use su"),
    get_pty: bool = Query(False, description="Whether to get a PTY"),
) -> CommonParams:
    """FastAPI dependency for common parameters"""
    return CommonParams(group=group, name=name, sudo=sudo, su=su, get_pty=get_pty)


class LocalParams(BaseModel):
    """Common parameters shared across command types"""

    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    group: Optional[str] = Field(default="all", description="Optional inventory group")
    name: Optional[str] = Field(default=None, description="Optional name")

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

        return f"CommonParams({', '.join(field_strings)})"


def local_params(
    group: Optional[str] = Query(
        "all", description="Optional inventory group (defaults to 'all')"
    ),
    name: Optional[str] = Query(None, description="Optional name"),
) -> LocalParams:
    """FastAPI dependency for common parameters"""
    return LocalParams(group=group, name=name)
