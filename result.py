# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from typing import Any, Dict, Optional
from asyncssh import SSHCompletedProcess
from command import Command

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
                 cp: Optional[SSHCompletedProcess] = None,
                 host: Optional[str] = None,
                 op: Optional[Command] = None,
                 changed: bool = False,
                 executed: bool = False,
                 output: Optional[list[Dict[str, str | Any]]] = [],
                 error: Optional[str] = None,
                 ) -> None:
        self.cp: Optional[SSHCompletedProcess] = cp
        self.host: Optional[str] = host
        self.op: Optional[Command] = op
        self.changed: bool = changed
        self.executed: bool = executed
        self.output: Optional[list[Dict[str, str | Any]]] = output
        self.error: Optional[str] = error

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
                f"output={self.output!r}, "
                f"error={self.error!r})")



