from typing import Optional
from typing import AsyncGenerator
from fastapi import Query
from pydantic import BaseModel, ConfigDict
from construction_tracker import track_yields
from reemote.command import Command
from reemote.response import Response

class LocalModel(BaseModel):
    """Common parameters shared across command types"""
    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    group: Optional[str] = "all"
    name: Optional[str] = None

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
def local_params(
    group: Optional[str] = Query(
        "all", description="Optional inventory group (defaults to 'all')"
    ),
    name: Optional[str] = Query(None, description="Optional name"),
) -> LocalModel:
    """FastAPI dependency for common parameters"""
    return LocalModel(group=group, name=name)

class Local:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    @track_yields
    async def execute(self) -> AsyncGenerator[Command, Response]:
        model_instance = self.Model(**self.kwargs)

        yield Command(
            local=True,
            callback=self._callback,
            call=str(model_instance),
            caller=model_instance
        )
