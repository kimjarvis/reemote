from typing import Dict, Optional, Callable

from wheel.macosx_libfile import segment_command_fields


class Operation:
    def __init__(self, command: str, guard: bool = True, local: bool = False, callback: Optional[Callable] = None, caller=None, sudo: bool = False, su: bool = False, composite=False):
        self.command: str = command
        self.guard: bool = guard
        self.host_info: Optional[Dict[str, str]] = None
        self.global_info: Optional[Dict[str, str]] = None
        self.local=local
        self.callback=callback
        self.caller=caller
        self.sudo=sudo
        self.su=su
        self.composite=composite

    def __str__(self) -> str:
        return repr(self)

    def __repr__(self) -> str:
        return (f"Operation(command={self.command!r}, "
                f"guard={self.guard!r}, "
                f"local={self.local!r}, "
                f"callback={self.callback!r}, "
                f"caller={self.caller!r}, "
                f"composite={self.composite!r}, "
                f"sudo={self.sudo!r}, su={self.su!r}), "
                f"host_info={self.host_info!r}, "
                f"global_info={self.global_info!r})")

def serialize_operation(obj):
    if isinstance(obj, Operation):  # Check if the object is of type Operation
        return {
            "command": obj.command,          # Serialize the command (string)
            "guard": obj.guard,              # Serialize the guard (boolean)
            "host_info": obj.host_info,      # Serialize the host_info (dict or None)
            "global_info": obj.global_info       # Serialize the global_info (dict or None)
        }
    else:
        raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")