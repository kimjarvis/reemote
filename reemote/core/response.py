# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from typing import Any, Dict, Optional, Tuple, Union, List
from pydantic import Field
from pydantic import BaseModel, RootModel

class SSHCompletedProcessModel(BaseModel):
    # env: Optional[Dict[str, str]] = Field(
    #     default=None,
    #     description="The environment the client requested to be set for the process."
    # )
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
        # "env": getattr(ssh_completed_process, "env", None),
        "command": getattr(ssh_completed_process, "command", None),
        "subsystem": getattr(ssh_completed_process, "subsystem", None),
        "exit_status": getattr(ssh_completed_process, "exit_status", None),
        "exit_signal": getattr(ssh_completed_process, "exit_signal", None),
        "returncode": getattr(ssh_completed_process, "returncode", None),
        "stdout": getattr(ssh_completed_process, "stdout", None),
        "stderr": getattr(ssh_completed_process, "stderr", None),
    }


class Response(BaseModel):
    cp: Optional[SSHCompletedProcessModel] = Field(
        default=None,
        description="The results from the executed command."
    )
    host: Optional[str] = Field (default=None, description="The host the command was executed on.")
    value: Optional[Any] = None  # Accept any type


class ResponseElement(BaseModel):
    host: str = Field(default="", description="The host the command was executed on")
    changed: bool = Field(default=False, description="Whether the host changed")
    error: bool = Field(default=False, description="Whether or not there was an error")
    value: Optional[str] = Field(default=None, description="Error message")

class ResponseModel(RootModel[List[ResponseElement]]):
    pass



class ShellResponseElement(ResponseElement):
    value: SSHCompletedProcessModel = Field(
        default=None, description="The results from the executed command."
    )

class ShellResponseModel(RootModel[List[ShellResponseElement]]):
    pass
