# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from asyncssh import SSHCompletedProcess
from command import Command, serialize_command
from typing import Optional, Mapping, Tuple, Union, Dict, Any
from types import MappingProxyType
from base64 import b64encode
from asyncssh import SSHCompletedProcess


class Result:
    """
    Represents the result of executing a command on a remote host via SSH.

    This class encapsulates all information related to command execution,
    including the execution status, output, and any errors that occurred.

    Attributes:
        cp (SSHCompletedProcess): The completed SSH process containing
            execution details like stdout, stderr, and return code.
        host (str): The hostname or IP address of the target machine.
        op (Command): The command that was executed.
        changed (bool): Indicates if the operation resulted in changes.
        executed (bool): Indicates if the command was actually executed.
        error (str): Error message if the execution failed.
    """

    def __init__(self,
                 cp: SSHCompletedProcess = None,
                 host: str = None,
                 op: Command = None,
                 changed: bool = False,
                 executed: bool = False,
                 error: str = None,
                 ):
        self.cp = cp
        self.host = host
        self.op = op
        self.changed = changed
        self.executed = executed
        self.error = error

    def __str__(self) -> str:
        return repr(self)

    def __repr__(self) -> str:
        # Use helper variables for the ternary operations
        returncode = self.cp.returncode if self.cp else None
        stdout = self.cp.stdout if self.cp else None
        stderr = self.cp.stderr if self.cp else None

        return (f"Result(host={self.host!r}, "
                f"op={self.op!r}, "
                f"changed={self.changed!r}, "
                f"executed={self.executed!r}, "
                f"return code={returncode!r}, "
                f"stdout={stdout!r}, "
                f"stderr={stderr!r}, "
                f"error={self.error!r})")

    # Type alias for clarity
    BytesOrStr = Union[str, bytes]


def serialize_result(obj):
    if isinstance(obj, Result):
        return {
            "op":serialize_command(obj.op),
            "host": obj.host,
            "changed": obj.changed,
            "executed": obj.executed,
            "cp": serialize_cp(obj.cp),
            "error": obj.error,
        }
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")

def serialize_cp(obj: Optional[SSHCompletedProcess]) -> Dict[str, Any]:
    if obj is None:
        return None
    elif isinstance(obj, SSHCompletedProcess):
        env = dict(obj.env) if isinstance(obj.env, MappingProxyType) else obj.env

        # Convert bytes to Base64-encoded strings for stdout and stderr
        stdout = b64encode(obj.stdout).decode("utf-8") if isinstance(obj.stdout, bytes) else obj.stdout
        stderr = b64encode(obj.stderr).decode("utf-8") if isinstance(obj.stderr, bytes) else obj.stderr

        # Convert the exit_signal tuple to a list
        exit_signal = list(obj.exit_signal) if obj.exit_signal else None

        return {
            "env": env,
            "command": obj.command,
            "subsystem": obj.subsystem,
            "exit_status": obj.exit_status,
            "exit_signal": exit_signal,
            "returncode": obj.returncode,
            "stdout": stdout,
            "stderr": stderr,
        }
    else:
        raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")
