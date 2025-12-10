from typing import Optional, Dict, Callable, List, Tuple
from pydantic import BaseModel, Field, validator
import logging
from construction_tracker import track_construction, track_yields
from fastapi import Query


class CommonParams(BaseModel):
    """Common parameters shared across command types"""
    group: Optional[str] = Field(default="all", description="Optional inventory group")
    name: Optional[str] = Field(default=None, description="Optional name")
    sudo: bool = Field(default=False, description="Whether to use sudo")
    su: bool = Field(default=False, description="Whether to use su")
    get_pty: bool = Field(default=False, description="Whether to get a PTY")

    class Config:
        validate_assignment = True

    def __repr__(self) -> str:
        """Common parameters representation"""
        params = []
        if self.group and self.group != "all":
            params.append(f"group={self.group!r}")
        if self.name:
            params.append(f"name={self.name!r}")
        if self.sudo:
            params.append(f"sudo={self.sudo!r}")
        if self.su:
            params.append(f"su={self.su!r}")
        if self.get_pty:
            params.append(f"get_pty={self.get_pty!r}")

        if params:
            return f"CommonParams({', '.join(params)})"
        return "CommonParams()"

    def __str__(self) -> str:
        return self.__repr__()

    def common_repr(self) -> str:
        """Return just the common parameters string representation for use in subclasses"""
        common_str = self.__repr__().replace("CommonParams(", "").replace(")", "")
        if common_str:
            return common_str
        return ""

def common_params(
    group: Optional[str] = Query("all", description="Optional inventory group (defaults to 'all')"),
    name: Optional[str] = Query(None, description="Optional name"),
    sudo: bool = Query(False, description="Whether to use sudo"),
    su: bool = Query(False, description="Whether to use su"),
    get_pty: bool = Query(False, description="Whether to get a PTY")
) -> CommonParams:
    """FastAPI dependency for common parameters"""
    return CommonParams(group=group, name=name, sudo=sudo, su=su, get_pty=get_pty)
