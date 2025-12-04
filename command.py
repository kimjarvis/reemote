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
                 guard: bool = True,
                 local: bool = False,
                 callback: Optional[Callable] = None,
                 caller=None,
                 sudo: bool = False,
                 su: bool = False,
                 get_pty: bool =False):
        self.name = name
        self.command: str = command
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

from typing import Dict, Any, Optional, Callable

def serialize_command(obj: Command) -> Optional[Dict[str, Any]]:
    """
    Serializes a Command object into a dictionary.

    Args:
        obj (Command): The Command object to serialize.

    Returns:
        Optional[Dict[str, Any]]: A dictionary representation of the Command object,
                                  or None if the input is None.

    Raises:
        TypeError: If the input object is not of type Command.
    """
    if obj is None:
        return None
    elif isinstance(obj, Command):
        # Serialize all relevant attributes of the Command object
        return {
            "name": obj.name,
            "command": obj.command,
            "guard": obj.guard,
            "local": obj.local,
            "callback": str(obj.callback) if obj.callback else None,  # Serialize callback as string
            "caller": str(obj.caller) if obj.caller else None,        # Serialize caller as string
            "sudo": obj.sudo,
            "su": obj.su,
            "get_pty": obj.get_pty,
            "host_info": obj.host_info,  # Directly include the dictionary
            "global_info": obj.global_info,  # Directly include the dictionary
        }
    else:
        raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")