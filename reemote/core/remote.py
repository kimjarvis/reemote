from abc import abstractmethod
from typing import Optional, Dict, Any
from fastapi import Body
from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field

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


class Remote:
    Model = RemoteModel

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

        # Check if the subclass overrides the 'Model' field
        if cls.Model is Remote.Model:  # If it's still the same as the base class
            raise NotImplementedError(f"Class {cls.__name__} must override the 'Model' class field.")

        cls.child = cls.__name__  # Set the 'child' field to the name of the subclass

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        # Define the fields that are considered "common" based on RemoteParams
        common_fields = set(RemoteModel.model_fields.keys())

        # Separate kwargs into common_kwargs and extra_kwargs
        self.common_kwargs = {key: value for key, value in kwargs.items() if key in common_fields}
        self.extra_kwargs = {key: value for key, value in kwargs.items() if key not in common_fields}

    @abstractmethod
    async def execute(self):
        pass