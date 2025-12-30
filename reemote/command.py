from enum import Enum
from typing import Any, Callable, Dict, Optional

from pydantic import ConfigDict, Field, field_validator  # Updated imports

from reemote.models import CommonModel


class ConnectionType(Enum):
    LOCAL = 1
    REMOTE = 2
    PASSTHROUGH = 3

class Command(CommonModel):
    """Command model with validation using Pydantic"""

    model_config = ConfigDict(  # Replaces class Config
        validate_assignment=True,
        arbitrary_types_allowed=True,  # Needed for Callable and caller fields
        extra="forbid",  # Optional: add this to prevent extra fields
    )

    command: Optional[str] = Field(
        default=None, description="The command to execute (optional)"
    )
    call: Optional[str] = Field(
        default=None, description="The caller"
    )

    # Optional fields with defaults
    type: ConnectionType = Field(
        default=ConnectionType.REMOTE,
        description="The connection type to use"
    )
    callback: Optional[Callable] = Field(
        default=None, description="Optional callback function"
    )
    caller: Optional[object] = Field(default=None, description="Caller object")

    # Fields that will be populated later (not in __init__)
    host_info: Optional[Dict[str, str]] = Field(
        default=None, description="Host information", exclude=True
    )
    global_info: Optional[Dict[str, Any]] = Field(
        default=None, description="Global information", exclude=True
    )
    # Return only
    value: Optional[Any] = Field(
        default=None, description="Value to pass to response", exclude=True
    )
    changed: Optional[bool] = Field(
        default=True, description="Whether the host changed", exclude=True
    )
    error: Optional[bool] = Field(
        default=False, description="Whether there was an error", exclude=True
    )

    @field_validator("command")
    @classmethod
    def command_not_empty(cls, v: Optional[str]) -> Optional[str]:
        """Validate that if command is provided, it's not empty or whitespace only"""
        if v is not None:
            stripped = v.strip()
            if not stripped:
                raise ValueError("Command cannot be empty if provided")
            return stripped
        return v

    @field_validator("group")
    @classmethod
    def group_not_empty_if_provided(cls, v: Optional[str]) -> Optional[str]:
        """Validate group is not empty string if provided"""
        if v is not None and v == "":
            return "all"
        return v

def command_to_dict(command):
    return {
        "group": getattr(command, "group", "all"),
        "name": getattr(command, "name", None),
        "sudo": getattr(command, "sudo", False),
        "su": getattr(command, "su", False),
        "get_pty": getattr(command, "get_pty", False),
        "command": getattr(command, "command", None),
        "call": getattr(command, "call", None),
        "type": getattr(command, "type", None),
        "callback": getattr(command, "callback", None),
        "caller": getattr(command, "caller", None),
    }

