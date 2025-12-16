# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com
# This software is licensed under the MIT License. See the LICENSE file for details.
#
import logging
import sys
from typing import Any, Dict, Tuple, Optional, List, Union, Generator

from asyncssh import SSHCompletedProcess
from pydantic import BaseModel, Field, ConfigDict
from pydantic import field_validator

from command import Command


class PackageInfo(BaseModel):
    name: str
    version: str


class Response(BaseModel):
    # Core execution results (from original Result)
    cp: Optional[SSHCompletedProcess] = Field(default=None, exclude=True)
    host: Optional[str] = None
    op: Optional[Command] = Field(default=None, exclude=True)
    changed: bool = False
    output: Optional[Any] = None  # Accept any type
    executed: bool = True  # New field: indicates if the command was executed

    # Fields from Command (r.op)
    name: Optional[str] = None
    command: Optional[str] = None
    group: Optional[str] = None
    local: bool = False
    callback_str: Optional[str] = Field(None, alias="callback")
    caller_str: Optional[str] = Field(None, alias="caller")
    call: Optional[str] = Field(None, alias="call")
    sudo: bool = False
    su: bool = False
    get_pty: bool = False
    host_info: Optional[Dict[str, str]] = None
    global_info: Optional[Union[str, List[str]]] = None

    # Process fields (extracted from SSHCompletedProcess)
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    return_code: Optional[int] = Field(None, alias="returncode")
    env: Optional[Dict[str, str]] = None
    subsystem: Optional[str] = None
    exit_status: Optional[int] = None
    exit_signal: Optional[Tuple[str, bool, str, str]] = None
    stdout_bytes: Optional[bytes] = None
    stderr_bytes: Optional[bytes] = None

    # Pydantic v2 config
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        ignored_types=(Generator,),
    )

    def __init__(self, **data):
        # Extract fields from cp and op if provided
        cp = data.get("cp")
        op = data.get("op")

        # Populate process fields from cp
        if cp and isinstance(cp, SSHCompletedProcess):
            data["stdout"] = self._bytes_to_str(cp.stdout) if cp.stdout else None
            data["stderr"] = self._bytes_to_str(cp.stderr) if cp.stderr else None
            data["return_code"] = cp.returncode
            data["env"] = getattr(cp, "env", None)
            data["subsystem"] = getattr(cp, "subsystem", None)
            data["exit_status"] = getattr(cp, "exit_status", None)
            data["exit_signal"] = getattr(cp, "exit_signal", None)
            data["stdout_bytes"] = cp.stdout if isinstance(cp.stdout, bytes) else None
            data["stderr_bytes"] = cp.stderr if isinstance(cp.stderr, bytes) else None

        # Populate command fields from op
        if op and isinstance(op, Command):
            data["name"] = getattr(op, "name", None)
            data["command"] = getattr(op, "command", None)
            data["group"] = getattr(op, "group", None)
            data["local"] = getattr(op, "local", False)
            data["callback"] = self._callback_to_str(getattr(op, "callback", None))
            data["caller"] = self._caller_to_str(getattr(op, "caller", None))
            data["call"] = self._caller_to_str(getattr(op, "call", None))
            data["sudo"] = getattr(op, "sudo", False)
            data["su"] = getattr(op, "su", False)
            data["get_pty"] = getattr(op, "get_pty", False)
            data["host_info"] = getattr(op, "host_info", None)
            data["global_info"] = getattr(op, "global_info", None)

        super().__init__(**data)
        logging.info(f"{self}")

    @classmethod
    def from_command(cls, command: Command, **kwargs) -> "Response":
        """
        Create a Response from a Command object.

        Args:
            command: The Command object to copy information from
            **kwargs: Additional parameters to pass to Response constructor

        Returns:
            Response instance with command fields copied
        """
        # Start with the command fields
        data = {
            "op": command,
            "name": None,
            "command": None,
            "group": None,
            "local": False,
            "sudo": False,
            "su": False,
            "get_pty": False,
            "host_info": None,
            "global_info": None,
        }

        # Convert callback and caller to string representations
        data["callback"] = cls._callback_to_str(getattr(command, "callback", None))
        data["caller"] = cls._caller_to_str(getattr(command, "caller", None))

        # Update with any additional kwargs
        data.update(kwargs)

        c = cls(**data)
        logging.info(f"{c}")
        return c

    @staticmethod
    def _bytes_to_str(value: Any) -> str:
        """Convert bytes to string if needed."""
        if isinstance(value, bytes):
            try:
                return value.decode("utf-8")
            except UnicodeDecodeError:
                return str(value)
        return str(value) if value is not None else None

    @staticmethod
    def _callback_to_str(value: Any) -> Optional[str]:
        """Convert callback function to string representation."""
        if value is None:
            return None
        if callable(value):
            try:
                name = value.__name__
            except AttributeError:
                name = str(value)
            return f"<callback {name}>"
        return str(value)

    @staticmethod
    def _caller_to_str(value: Any) -> Optional[str]:
        """Convert callback function to string representation."""
        if value is None:
            return None
        if callable(value):
            try:
                name = value.__name__
            except AttributeError:
                name = str(value)
            return f"<caller {name}>"
        return str(value)

    @staticmethod
    def _make_json_serializable(value: Any) -> Any:
        """Convert non-JSON-serializable types to serializable ones."""
        if value is None:
            return None
        elif isinstance(value, (str, int, float, bool)):
            return value
        elif isinstance(value, (list, tuple)):
            return [Response._make_json_serializable(item) for item in value]
        elif isinstance(value, dict):
            return {k: Response._make_json_serializable(v) for k, v in value.items()}
        elif isinstance(value, bytes):
            try:
                return value.decode("utf-8")
            except UnicodeDecodeError:
                return str(value)
        elif callable(value):
            try:
                return f"<callback {value.__name__}>"
            except AttributeError:
                return f"<callback {type(value).__name__}>"
        else:
            try:
                return str(value)
            except (TypeError, ValueError) as e:
                logging.error(f"{e}", exc_info=True)
                sys.exit(1)

    # CHANGED: Pydantic V2 style validator
    @field_validator("global_info", mode="before")
    @classmethod
    def validate_global_info(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            return v
        elif isinstance(v, list):
            return [str(item) for item in v]
        else:
            return str(v)

    def __str__(self) -> str:
        """String representation for debugging."""
        return self.__repr__()

    def __repr__(self) -> str:
        """Detailed representation."""
        return_code = self.cp.returncode if self.cp else self.return_code
        stdout = self.cp.stdout if self.cp else self.stdout
        stderr = self.cp.stderr if self.cp else self.stderr

        if self.executed and self.local:
            return(
                f"Response(host={self.host!r}, "
                f"call={self.call!r}, "
                f"changed={self.changed!r}, "                
                f"output={self.output!r})"
            )
        else:
            return (
                f"Response(host={self.host!r}, "
                f"group={self.group!r}, "
                f"name={self.name!r}, "
                f"call={self.call!r}, "
                f"command={self.command!r}, "
                f"changed={self.changed!r}, "
                f"executed={self.executed!r}, "
                f"return_code={return_code!r}, "
                f"stdout={stdout!r}, "
                f"stderr={stderr!r}, "
                f"output={self.output!r})"
            )


async def validate_responses(responses: list[Any]) -> list[Response]:
    """Convert any response-like objects to UnifiedResult instances."""
    validated_responses = []

    for r in responses:
        try:
            if isinstance(r, Response):
                # Already a UnifiedResult, just add it
                validated_responses.append(r)
            else:
                # Convert to UnifiedResult
                unified_result = Response(
                    cp=getattr(r, "cp", None),
                    host=getattr(r, "host", None),
                    op=getattr(r, "op", None),
                    changed=getattr(r, "changed", False),
                    executed=getattr(r, "executed", True),  # Include executed field
                    output=getattr(r, "output", []),
                )
                validated_responses.append(unified_result)
        except Exception as e:
            logging.error(f"Error converting response: {e}", exc_info=True)
            # Create a minimal error result
            error_result = Response(
                host=getattr(r, "host", None) if hasattr(r, "host") else None,
                executed=getattr(r, "executed", True)
                if hasattr(r, "executed")
                else True,  # Include executed field
            )
            validated_responses.append(error_result)

    return validated_responses
