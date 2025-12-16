from typing import Optional
from typing import AsyncGenerator
from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field
from construction_tracker import track_yields
from command import Command
from response import Response

class RemoteParams(BaseModel):
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

# Used by api
def remote_params(
    group: Optional[str] = Query(
        "all", description="Optional inventory group (defaults to 'all')"
    ),
    name: Optional[str] = Query(None, description="Optional name"),
    sudo: bool = Query(False, description="Whether to use sudo"),
    su: bool = Query(False, description="Whether to use su"),
    get_pty: bool = Query(False, description="Whether to get a PTY"),
) -> RemoteParams:
    """FastAPI dependency for common parameters"""
    return RemoteParams(group=group, name=name, sudo=sudo, su=su, get_pty=get_pty)

class RemoteModel:
    def __init__(self, **kwargs):
        # Pass all kwargs directly to be validated by the model
        self._data = kwargs
        self.extra_kwargs = {}

    @track_yields
    async def execute(self) -> AsyncGenerator[Command, Response]:
        # All validation happens here
        model_instance = self.Model(**self._data)

        yield Command(
            command=model_instance.cmd,
            call=str(model_instance),
            **self.extra_kwargs
        )
