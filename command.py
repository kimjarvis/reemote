# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from typing import Dict, Optional, Callable

class Command:
    """
    Represents a command to be executed on a remote or local host.

    This class encapsulates all the information needed to execute a command,
    including the command string, execution options, and contextual information.

    Attributes:
        command (str): The actual command string to be executed.
        guard (bool): If True, enables guard mode which may prevent execution
                     under certain conditions. Defaults to True.
        host_info (Optional[Dict[str, str]]): Host-specific information and variables.
        global_info (Optional[Dict[str, str]]): Global information and variables
                                               accessible across all hosts.
        local (bool): If True, the command will be executed locally instead of
                     on a remote host. Defaults to False.
        callback (Optional[Callable]): A function to be called after command execution.
        caller: Reference to the entity that initiated the command.
        sudo (bool): If True, the command will be executed with sudo privileges.
                    Defaults to False.
        su (bool): If True, the command will be executed with su privileges.
                  Defaults to False.
    """

    def __init__(self,
                 name: str = "",
                 command: str = "",
                 group: str = "",
                 guard: bool = True,
                 local: bool = False,
                 callback: Optional[Callable] = None,
                 caller=None,
                 sudo: bool = False,
                 su: bool = False,
                 get_pty: bool =False):
        self.name = name
        self.command: str = command
        self.group = group
        self.guard: bool = guard
        self.host_info: Optional[Dict[str, str]] = None
        self.global_info: Optional[Dict[str, str]] = None
        self.local=local
        self.callback=callback
        self.caller=caller
        self.sudo=sudo
        self.su=su
        self.get_pty=get_pty


    def __str__(self) -> str:
        return repr(self)

    def __repr__(self) -> str:
        return (f"Command("
                f"name={self.name!r}, "
                f"command={self.command!r}, "
                f"group={self.group!r}, "
                f"guard={self.guard!r}, "
                f"local={self.local!r}, "
                f"callback={self.callback!r}, "
                f"caller={self.caller!r}, "
                f"sudo={self.sudo!r}, "
                f"su={self.su!r}, "
                f"get_pty={self.get_pty!r}, "
                f"host_info={self.host_info!r}, "
                f"global_info={self.global_info!r}"
                f")")

