from typing import Optional
from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field

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

# Used by api
def remote_params(
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

class Remote:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        # Define the fields that are considered "common" based on RemoteParams
        common_fields = set(RemoteModel.model_fields.keys())

        # Separate kwargs into common_kwargs and extra_kwargs
        self.common_kwargs = {key: value for key, value in kwargs.items() if key in common_fields}
        self.extra_kwargs = {key: value for key, value in kwargs.items() if key not in common_fields}


