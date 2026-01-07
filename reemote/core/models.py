from pathlib import PurePath
from typing import Optional, Union, Dict, Any
from fastapi import Body
from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

class RemoteModel(BaseModel):
    """Common parameters shared across command types"""

    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    group: Optional[str] = Field(
        default="all", description="Optional inventory group (defaults to 'all')."
    )
    name: Optional[str] = Field(default=None, description="Optional name.")
    sudo: bool = Field(default=False, description="Execute command with sudo.")
    su: bool = Field(default=False, description="Execute command with su.")
    connection: Optional[Dict[str, Any]] = Field(
        default={},
        description="Optional connection arguments to pass to Asyncssh create_session().",
    )
    session: Optional[Dict[str, Any]] = Field(
        default={},
        description="Optional session arguments to pass to Asyncssh create_session().",
    )


def remotemodel(
    group: Optional[str] = Query(
        "all", description="Optional inventory group (defaults to 'all')"
    ),
    name: Optional[str] = Query(None, description="Optional name"),
    sudo: bool = Query(False, description="Whether to use sudo"),
    su: bool = Query(False, description="Whether to use su"),
    connection: Optional[Dict[str, Any]] = Body(
        default={},
        description="Optional connection arguments to pass to Asyncssh create_session().",
    ),
    session: Optional[Dict[str, Any]] = Body(
        default={},
        description="Optional session arguments to pass to Asyncssh create_session().",
    ),
) -> RemoteModel:
    """FastAPI dependency for common parameters"""
    return RemoteModel(group=group, name=name, sudo=sudo, su=su, connection=connection, session=session)
