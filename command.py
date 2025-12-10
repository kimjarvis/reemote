import logging
from typing import Dict, Optional, Callable
from construction_tracker import track_construction, track_yields

@track_construction
class Command:
    def __init__(self,
                 name: str = "",
                 command: str = "",
                 group: str = "",
                 local: bool = False,
                 callback: Optional[Callable] = None,
                 caller=None,
                 sudo: bool = False,
                 su: bool = False,
                 get_pty: bool = False,
                 id: Optional[int] = None,
                 parents: Optional[list[(int,str)]] = None):
        self.id = id
        self.parents: Optional[list[(int,str)]] = parents
        self.name = name
        self.command: str = command
        self.group = group
        self.host_info: Optional[Dict[str, str]] = None
        self.global_info: Optional[Dict[str, str]] = None
        self.local = local
        self.callback = callback
        self.caller = caller
        self.sudo = sudo
        self.su = su
        self.get_pty = get_pty

    def __str__(self) -> str:
        return repr(self)

    def __repr__(self) -> str:
        return (f"Command("
                f"id={self.id!r}, "
                f"parents={self.parents!r}, "
                f"name={self.name!r}, "
                f"command={self.command!r}, "
                f"group={self.group!r}, "
                f"local={self.local!r}, "
                f"callback={self.callback!r}, "
                f"caller={self.caller!r}, "
                f"sudo={self.sudo!r}, "
                f"su={self.su!r}, "
                f"get_pty={self.get_pty!r}, "
                f"host_info={self.host_info!r}, "
                f"global_info={self.global_info!r}"
                f")")