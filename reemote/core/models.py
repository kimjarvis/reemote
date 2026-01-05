from pathlib import PurePath
from typing import Optional, Union, Dict, Any

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from reemote.core.inventory_model import Connection, InventoryItem, Inventory, Session

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


class RemoteModel(BaseModel):
    """Common parameters shared across command types"""

    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    group: Optional[str] = Field(
        default="all", description="Optional inventory group (defaults to 'all')."
    )
    name: Optional[str] = Field(default=None, description="Optional name.")
    sudo: bool = Field(default=False, description="Execute command with sudo.")
    su: bool = Field(default=False, description="Execute command with su.")
    # connection: Optional[Dict[str, Any]] = Field(
    #     default_factory=Connection,
    #     description="Optional connection arguments to pass to Asyncssh create_session().",
    # )
    # session: Optional[Dict[str, Any]] = Field(
    #     default_factory=Session,
    #     description="Optional session arguments to pass to Asyncssh create_session().",
    # )


def remotemodel(
    group: Optional[str] = Query(
        "all", description="Optional inventory group (defaults to 'all')"
    ),
    name: Optional[str] = Query(None, description="Optional name"),
    sudo: bool = Query(False, description="Whether to use sudo"),
    su: bool = Query(False, description="Whether to use su"),
    # connection: Optional[Dict[str, Any]] = Query(
    #     default_factory=Connection,
    #     description="Optional connection arguments to pass to Asyncssh create_session().",
    # ),
    # session: Optional[Dict[str, Any]] = Query(
    #     default_factory=Session,
    #     description="Optional session arguments to pass to Asyncssh create_session().",
    # ),
) -> RemoteModel:
    """FastAPI dependency for common parameters"""
    # return RemoteModel(group=group, name=name, sudo=sudo, su=su, connection=connection, session=session)
    return RemoteModel(group=group, name=name, sudo=sudo, su=su)
