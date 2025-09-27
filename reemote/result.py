from asyncssh import SSHCompletedProcess
from reemote.operation import Operation
from typing import Optional, Mapping, Tuple, Union
from types import MappingProxyType
from base64 import b64encode
from asyncssh import SSHCompletedProcess
from reemote.operation import serialize_operation


class Result:

    def __init__(self,
                 cp: SSHCompletedProcess = None,
                 host: str = None,
                 op: Operation = None,
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
    # print(f"Serializing object of type {obj.__class__.__name__}: {obj}")
    if isinstance(obj, Result):
        # print(f"Result attributes: {vars(obj)}")
        return {
            "host": obj.host,
            "op": serialize_operation(obj.op) if obj.op else None,
            "changed": obj.changed,
            "executed": obj.executed,
            "cp": serialize_cp(obj.cp),
            "error": obj.error,
        }
    elif isinstance(obj, Operation):
        # print(f"Operation attributes: {vars(obj)}")
        return {
            "command": obj.command,
            "guard": obj.guard,
            "host_info": obj.host_info,
            "global_info": obj.global_info,
        }
    else:
        raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")


def serialize_cp(obj):
    if obj is None:
        return None
    elif isinstance(obj, SSHCompletedProcess):
        # Convert mappingproxy to dict if necessary
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