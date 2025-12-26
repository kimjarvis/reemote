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


    # def __repr__(self) -> str:
    #     """Use detailed_repr for representation"""
    #     return self.detailed_repr()
    #
    # def __str__(self) -> str:
    #     return self.__repr__()
    #
    # def detailed_repr(self) -> str:
    #     """Show all fields including defaults"""
    #     field_strings = []
    #
    #     # Add common parameters from parent
    #     if self.group is not None:
    #         field_strings.append(f"group={self.group!r}")
    #     if self.name is not None:
    #         field_strings.append(f"name={self.name!r}")
    #     field_strings.append(f"sudo={self.sudo!r}")
    #     field_strings.append(f"su={self.su!r}")
    #     field_strings.append(f"get_pty={self.get_pty!r}")
    #
    #     # Add command-specific fields
    #     if self.command is not None:
    #         field_strings.append(f"command={self.command!r}")
    #     # Add command-specific fields
    #     if self.call is not None:
    #         field_strings.append(f"call={self.call!r}")
    #
    #     field_strings.append(f"type={self.type!r}")
    #     if self.value is not None:
    #         field_strings.append(f"value={self.value!r}")
    #     if self.changed is not None:
    #         field_strings.append(f"changed={self.changed!r}")
    #
    #     if self.callback is not None:
    #         callback_repr = (
    #             f"<function {self.callback.__name__}>"
    #             if hasattr(self.callback, "__name__")
    #             else repr(self.callback)
    #         )
    #         field_strings.append(f"callback={callback_repr}")
    #
    #     if self.caller is not None:
    #         caller_repr = (
    #             f"<{type(self.caller).__name__} object>"
    #             if hasattr(self.caller, "__class__")
    #             else repr(self.caller)
    #         )
    #         field_strings.append(f"caller={caller_repr}")
    #
    #     if self.host_info is not None:
    #         field_strings.append(f"host_info={self.host_info!r}")
    #
    #     if self.global_info is not None:
    #         field_strings.append(f"global_info={self.global_info!r}")
    #
    #     return f"Command({', '.join(field_strings)})"
