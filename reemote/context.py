from enum import Enum
from typing import Any, Callable, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator  # Updated imports

from reemote.core.remote import RemoteModel
from reemote.core.inventory_model import InventoryItem


class ConnectionType(Enum):
    LOCAL = 1
    REMOTE = 2
    PASSTHROUGH = 3


class Context(RemoteModel):
    model_config = ConfigDict(  # Replaces class Config
        validate_assignment=True,
        arbitrary_types_allowed=True,  # Needed for Callable and caller fields
        extra="forbid",  # Optional: add this to prevent extra fields
    )

    command: Optional[str] = Field(
        default=None, description="The command to execute (optional)", exclude=True
    )
    call: Optional[str] = Field(default=None, description="The caller", exclude=True)

    # Optional fields with defaults
    type: ConnectionType = Field(
        default=ConnectionType.REMOTE,
        description="The connection type to use",
        exclude=True,
    )
    callback: Optional[Callable] = Field(
        default=None, description="Optional callback function", exclude=True
    )
    caller: Optional[object] = Field(
        default=None, description="Caller object", exclude=True
    )

    inventory_item: Optional[InventoryItem] = Field(
        default=None, description="Inventory item"
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

    def to_json_serializable(self) -> Dict[str, Any]:
        return self.model_dump()
