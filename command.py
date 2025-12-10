from typing import Optional, Dict, Callable, List, Tuple, Any
from pydantic import Field, validator  # Make sure Field is imported
from common_params import CommonParams
from construction_tracker import track_construction


@track_construction
class Command(CommonParams):
    """Command model with validation using Pydantic"""

    # Required fields
    command: str = Field(..., description="The command to execute")

    # Optional fields with defaults
    local: bool = Field(default=False, description="Whether to run locally")
    callback: Optional[Callable] = Field(default=None, description="Optional callback function")
    caller: Optional[object] = Field(default=None, description="Caller object")
    id: Optional[int] = Field(default=None, description="Command ID")
    parents: Optional[List[Tuple[int, str]]] = Field(
        default=None,
        description="List of parent commands (id, name) pairs"
    )

    # Fields that will be populated later (not in __init__)
    host_info: Optional[Dict[str, str]] = Field(
        default=None,
        description="Host information",
        exclude=True
    )
    global_info: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Global information",
        exclude=True
    )

    class Config:
        validate_assignment = True
        arbitrary_types_allowed = True  # Needed for Callable and caller fields

    @validator('command')
    def command_not_empty(cls, v):
        """Validate that command is not empty or whitespace only"""
        if not v or not v.strip():
            raise ValueError('Command cannot be empty')
        return v.strip()

    @validator('group')
    def group_not_empty_if_provided(cls, v):
        """Validate group is not empty string if provided"""
        if v is not None and v == "":
            return "all"
        return v

    def __repr__(self) -> str:
        """Command representation that includes common parameters"""
        # Start with the common parameters
        common_str = self.common_repr()

        # Build command-specific parameters
        cmd_params = []

        # Add required command field
        if self.command:
            cmd_params.append(f"command={self.command!r}")

        # Add optional fields that have values
        if self.id is not None:
            cmd_params.append(f"id={self.id!r}")
        if self.parents:
            cmd_params.append(f"parents={self.parents!r}")
        if self.local:
            cmd_params.append(f"local={self.local!r}")
        if self.callback:
            # For callable, show a simple representation
            callback_repr = f"<function {self.callback.__name__}>" if hasattr(self.callback, '__name__') else repr(
                self.callback)
            cmd_params.append(f"callback={callback_repr}")
        if self.caller:
            caller_repr = f"<{type(self.caller).__name__} object>" if hasattr(self.caller, '__class__') else repr(
                self.caller)
            cmd_params.append(f"caller={caller_repr}")
        if self.host_info:
            cmd_params.append(f"host_info={self.host_info!r}")
        if self.global_info:
            cmd_params.append(f"global_info={self.global_info!r}")

        # Combine all parameters
        all_params = []
        if common_str:
            all_params.append(common_str)
        all_params.extend(cmd_params)

        return f"Command({', '.join(all_params)})"

    def __str__(self) -> str:
        return self.__repr__()

    def detailed_repr(self) -> str:
        """Alternative representation showing all fields including defaults"""
        return super().__repr__()
