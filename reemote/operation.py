from typing import Dict, Optional, Callable

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


def word_wrap(text, width=40):
    wrapped_lines = []

    # Split the text into lines based on existing newlines
    for line in text.splitlines():
        # Handle each line separately
        current_line = ""
        words = line.split()

        for word in words:
            # Check if adding the next word exceeds the width
            if len(current_line) + len(word) + 1 <= width:
                # Add the word to the current line (with a space if not empty)
                current_line += (" " + word if current_line else word)
            else:
                # Append the current line to the result and start a new line
                wrapped_lines.append(current_line)
                current_line = word

        # Append any remaining text in the current line
        if current_line:
            wrapped_lines.append(current_line)

    # Join all wrapped lines with newlines and return
    return "\n".join(wrapped_lines)

def serialize_operation(obj):
    if isinstance(obj, Operation):  # Check if the object is of type Operation
        return {
            "command": word_wrap(obj.command),          # Serialize the command (string)
            "guard": obj.guard,              # Serialize the guard (boolean)
            "host_info": obj.host_info,      # Serialize the host_info (dict or None)
            "global_info": obj.global_info       # Serialize the global_info (dict or None)
        }
    else:
        raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")