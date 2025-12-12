from typing import Optional, Dict, Callable, Any
from pydantic import Field, validator  # Make sure Field is imported
from common_params import CommonParams
from construction_tracker import track_construction



@track_construction
class Command(CommonParams):
    """Command model with validation using Pydantic"""

    command: Optional[str] = Field(
        default=None,
        description="The command to execute (optional)"
    )

    # Optional fields with defaults
    local: bool = Field(default=False, description="Whether to run locally")
    callback: Optional[Callable] = Field(default=None, description="Optional callback function")
    caller: Optional[object] = Field(default=None, description="Caller object")

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
        """Validate that if command is provided, it's not empty or whitespace only"""
        if v is not None:
            stripped = v.strip()
            if not stripped:
                raise ValueError('Command cannot be empty if provided')
            return stripped
        return v

    @validator('group')
    def group_not_empty_if_provided(cls, v):
        """Validate group is not empty string if provided"""
        if v is not None and v == "":
            return "all"
        return v

    def __repr__(self) -> str:
        """Use detailed_repr for representation"""
        return self.detailed_repr()

    def __str__(self) -> str:
        return self.__repr__()

    def detailed_repr(self) -> str:
        """Show all fields including defaults"""
        field_strings = []

        # Add common parameters from parent
        if self.group is not None:
            field_strings.append(f"group={self.group!r}")
        if self.name is not None:
            field_strings.append(f"name={self.name!r}")
        field_strings.append(f"sudo={self.sudo!r}")
        field_strings.append(f"su={self.su!r}")
        field_strings.append(f"get_pty={self.get_pty!r}")

        # Add command-specific fields
        if self.command is not None:
            field_strings.append(f"command={self.command!r}")

        field_strings.append(f"local={self.local!r}")

        if self.callback is not None:
            callback_repr = f"<function {self.callback.__name__}>" if hasattr(self.callback, '__name__') else repr(
                self.callback)
            field_strings.append(f"callback={callback_repr}")

        if self.caller is not None:
            caller_repr = f"<{type(self.caller).__name__} object>" if hasattr(self.caller, '__class__') else repr(
                self.caller)
            field_strings.append(f"caller={caller_repr}")

        if self.host_info is not None:
            field_strings.append(f"host_info={self.host_info!r}")

        if self.global_info is not None:
            field_strings.append(f"global_info={self.global_info!r}")

        return f"Command({', '.join(field_strings)})"