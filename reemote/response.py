# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com
# This software is licensed under the MIT License. See the LICENSE file for details.
#
import logging
import sys
from typing import Any, Dict, Tuple, Optional, List, Union, Generator

from asyncssh import SSHCompletedProcess
from pydantic import BaseModel, Field, ConfigDict
from pydantic import field_validator

from reemote.command import Command, ConnectionType

class PackageInfo(BaseModel):
    name: str
    version: str

from typing import Dict, Optional, Tuple, Union
from pydantic import BaseModel, Field

class SSHCompletedProcessModel(BaseModel):
    """
    Results from running an SSH process.

    This object is returned by the run method on SSHClientConnection when the requested command has finished running.
    """
    env: Optional[Dict[str, str]] = Field(
        default=None,
        description="The environment the client requested to be set for the process."
    )
    command: Optional[str] = Field(
        default=None,
        description="The command the client requested the process to execute (if any)."
    )
    subsystem: Optional[str] = Field(
        default=None,
        description="The subsystem the client requested the process to open (if any)."
    )
    exit_status: int = Field(
        description="The exit status returned, or -1 if an exit signal is sent."
    )
    exit_signal: Optional[Tuple[str, bool, str, str]] = Field(
        default=None,
        description="The exit signal sent (if any) in the form of a tuple containing "
                    "the signal name, a bool for whether a core dump occurred, a message "
                    "associated with the signal, and the language the message was in."
    )
    returncode: int = Field(
        description="The exit status returned, or negative of the signal number when an exit signal is sent."
    )
    stdout: Union[str, bytes, None] = Field(
        default=None,
        description="The output sent by the process to stdout (if not redirected)."
    )
    stderr: Union[str, bytes, None] = Field(
        default=None,
        description="The output sent by the process to stderr (if not redirected)."
    )

def ssh_completed_process_to_dict(ssh_completed_process):
    return {
        "env": getattr(ssh_completed_process, "env", None),
        "command": getattr(ssh_completed_process, "command", None),
        "subsystem": getattr(ssh_completed_process, "subsystem", None),
        "exit_status": getattr(ssh_completed_process, "exit_status", None),
        "exit_signal": getattr(ssh_completed_process, "exit_signal", None),
        "returncode": getattr(ssh_completed_process, "returncode", None),
        "stdout": getattr(ssh_completed_process, "stdout", None),
        "stderr": getattr(ssh_completed_process, "stderr", None),
    }

class Response(BaseModel):
    # Core execution results (from original Result)
    cp: Optional[SSHCompletedProcessModel] = Field(
        default=None,
        description="The results from the executed command."
    )
    host: Optional[str] = Field (default=None, description="The host the command was executed on.")
    # op: Optional[Command] = Field(default=None, exclude=True)
    value: Optional[Any] = None  # Accept any type
    # changed: Optional[bool] = True
    # executed: Optional[bool] = True
    # success: Optional[bool] = True

    # Fields from Command (r.op)
    # name: Optional[str] = None
    # command: Optional[str] = None
    # group: Optional[str] = None
    # type: ConnectionType = None
    # callback_str: Optional[str] = Field(None, alias="callback")
    # caller_str: Optional[str] = Field(None, alias="caller")
    # call: Optional[str] = Field(None, alias="call")
    # sudo: bool = False
    # su: bool = False
    # get_pty: bool = False
    # host_info: Optional[Dict[str, str]] = None
    # global_info: Optional[Union[str, List[str]]] = None

    # Process fields (extracted from SSHCompletedProcess)
    # stdout: Optional[str] = None
    # stderr: Optional[str] = None
    # return_code: Optional[int] = Field(None, alias="returncode")
    # env: Optional[Dict[str, str]] = None
    # subsystem: Optional[str] = None
    # exit_status: Optional[int] = None
    # exit_signal: Optional[Tuple[str, bool, str, str]] = None
    # stdout_bytes: Optional[bytes] = None
    # stderr_bytes: Optional[bytes] = None

    # # Pydantic v2 config
    # model_config = ConfigDict(
    #     arbitrary_types_allowed=True,
    #     ignored_types=(Generator,),
    # )

    # def __init__(self, **data):
    #     # Extract fields from cp and op if provided
    #     cp = data.get("cp")
    #     op = data.get("op")
    #
    #     # Populate process fields from cp
    #     if cp and isinstance(cp, SSHCompletedProcess):
    #         data["stdout"] = self._bytes_to_str(cp.stdout) if cp.stdout else None
    #         data["stderr"] = self._bytes_to_str(cp.stderr) if cp.stderr else None
    #         data["return_code"] = cp.returncode
    #         data["env"] = getattr(cp, "env", None)
    #         data["subsystem"] = getattr(cp, "subsystem", None)
    #         data["exit_status"] = getattr(cp, "exit_status", None)
    #         data["exit_signal"] = getattr(cp, "exit_signal", None)
    #         data["stdout_bytes"] = cp.stdout if isinstance(cp.stdout, bytes) else None
    #         data["stderr_bytes"] = cp.stderr if isinstance(cp.stderr, bytes) else None
    #
    #     # Populate command fields from op
    #     if op and isinstance(op, Command):
    #         data["name"] = getattr(op, "name", None)
    #         data["command"] = getattr(op, "command", None)
    #         data["group"] = getattr(op, "group", None)
    #         data["type"] = getattr(op, "type", None)
    #         data["callback"] = self._callback_to_str(getattr(op, "callback", None))
    #         data["caller"] = self._caller_to_str(getattr(op, "caller", None))
    #         data["call"] = self._caller_to_str(getattr(op, "call", None))
    #
    #     # data["changed"] = getattr(op, "changed", None) ??
    #     data["sudo"] = getattr(op, "sudo", False)
    #     data["su"] = getattr(op, "su", False)
    #     data["get_pty"] = getattr(op, "get_pty", False)
    #     data["host_info"] = getattr(op, "host_info", None)
    #     data["global_info"] = getattr(op, "global_info", None)
    #
    #     super().__init__(**data)

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

        # Update with any additional kwargs
        data.update(kwargs)
        return cls(**data)
    #
    # @staticmethod
    # def _bytes_to_str(value: Any) -> str:
    #     """Convert bytes to string if needed."""
    #     if isinstance(value, bytes):
    #         try:
    #             return value.decode("utf-8")
    #         except UnicodeDecodeError:
    #             return str(value)
    #     return str(value) if value is not None else None
    #
    # @staticmethod
    # def _callback_to_str(value: Any) -> Optional[str]:
    #     """Convert callback function to string representation."""
    #     if value is None:
    #         return None
    #     if callable(value):
    #         try:
    #             name = value.__name__
    #         except AttributeError:
    #             name = str(value)
    #         return f"<callback {name}>"
    #     return str(value)
    #
    # @staticmethod
    # def _caller_to_str(value: Any) -> Optional[str]:
    #     """Convert callback function to string representation."""
    #     if value is None:
    #         return None
    #     if callable(value):
    #         try:
    #             name = value.__name__
    #         except AttributeError:
    #             name = str(value)
    #         return f"<caller {name}>"
    #     return str(value)

    # @staticmethod
    # def _make_json_serializable(value: Any) -> Any:
    #     """Convert non-JSON-serializable types to serializable ones."""
    #     if value is None:
    #         return None
    #     elif isinstance(value, (str, int, float, bool)):
    #         return value
    #     elif isinstance(value, (list, tuple)):
    #         return [Response._make_json_serializable(item) for item in value]
    #     elif isinstance(value, dict):
    #         return {k: Response._make_json_serializable(v) for k, v in value.items()}
    #     elif isinstance(value, bytes):
    #         try:
    #             return value.decode("utf-8")
    #         except UnicodeDecodeError:
    #             return str(value)
    #     elif callable(value):
    #         try:
    #             return f"<callback {value.__name__}>"
    #         except AttributeError:
    #             return f"<callback {type(value).__name__}>"
    #     else:
    #         try:
    #             return str(value)
    #         except (TypeError, ValueError) as e:
    #             logging.error(f"{e}", exc_info=True)
    #             raise

    # # CHANGED: Pydantic V2 style validator
    # @field_validator("global_info", mode="before")
    # @classmethod
    # def validate_global_info(cls, v):
    #     if v is None:
    #         return None
    #     if isinstance(v, str):
    #         return v
    #     elif isinstance(v, list):
    #         return [str(item) for item in v]
    #     else:
    #         return str(v)
    #
    # def __str__(self) -> str:
    #     """String representation for debugging."""
    #     return self.__repr__()
    #
    # def __repr__(self) -> str:
    #     """Detailed representation."""
    #     return_code = self.cp.returncode if self.cp else self.return_code
    #     stdout = self.cp.stdout if self.cp else self.stdout
    #     stderr = self.cp.stderr if self.cp else self.stderr
    #
    #     if self.type == ConnectionType.PASSTHROUGH:
    #         return(
    #             f"Response(host={self.host!r}, "
    #             # f"executed={self.executed!r}, "
    #             # f"success={self.success!r}, "
    #             f"call={self.call!r}, "
    #             f"changed={self.changed!r}, "
    #             # f"value={self.value!r})"
    #             f"value={self.value!r})"
    #         )
    #     elif  self.type == ConnectionType.LOCAL:
    #         return(
    #             # f"Response(host={self.host!r}, "
    #             # f"call={self.call!r}, "
    #             # f"changed={self.changed!r}, "
    #             # f"value={self.value!r})"
    #
    #             f"Response(host={self.host!r}, "
    #             f"group={self.group!r}, "
    #             f"name={self.name!r}, "
    #             # f"executed={self.executed!r}, "
    #             # f"success={self.success!r}, "
    #             f"call={self.call!r}, "
    #             f"command={self.command!r}, "
    #             f"changed={self.changed!r}, "
    #             f"return_code={return_code!r}, "
    #             f"stdout={stdout!r}, "
    #             f"stderr={stderr!r}, "
    #             f"value={self.value!r})"
    #
    #         )
    #     elif self.type == ConnectionType.REMOTE:
    #         return (
    #             f"Response(host={self.host!r}, "
    #             f"group={self.group!r}, "
    #             f"name={self.name!r}, "
    #             # f"executed={self.executed!r}, "
    #             # f"success={self.success!r}, "
    #             f"call={self.call!r}, "
    #             f"command={self.command!r}, "
    #             f"changed={self.changed!r}, "
    #             f"return_code={return_code!r}, "
    #             f"stdout={stdout!r}, "
    #             f"stderr={stderr!r}, "
    #             f"value={self.value!r})"
    #         )
    #     else:
    #         return (
    #             f"invalid !!! "
    #             f"Response(host={self.host!r}, "
    #             f"group={self.group!r}, "
    #             f"name={self.name!r}, "
    #             # f"executed={self.executed!r}, "
    #             # f"success={self.success!r}, "
    #             f"call={self.call!r}, "
    #             f"command={self.command!r}, "
    #             f"changed={self.changed!r}, "
    #             f"return_code={return_code!r}, "
    #             f"stdout={stdout!r}, "
    #             f"stderr={stderr!r}, "
    #             f"value={self.value!r})"
    #         )


async def validate_responses(responses: list[Any]) -> list[Response]:
    """
    Validate that all items in the list are instances of Response.
    Raises a ValueError if any item is not a valid Response instance.
    """
    validated_responses = []

    for r in responses:
        if not isinstance(r, Response):
            raise ValueError(f"Invalid response type: expected Response, got {type(r).__name__}")

        # Validate the existing Response instance
        validated_response = Response.model_validate(r)
        validated_responses.append(validated_response)

    return validated_responses