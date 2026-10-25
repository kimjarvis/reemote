"""reemote5: a POC model for generator-based two-phase execution.

An alternative to pyinfra's prepare/execute design in which deploy scripts
are generators yielding ``OperationRequest`` descriptors, and the driver
re-invokes the generator per phase, advancing all hosts in lockstep with a
fresh ``FactCache`` per step. Restricted to command generation: no SSH
transport, but the command-execution path (``send_command`` and the apply
drivers) and fact gathering (each fact's ``process``, ``FactCache.get``, the
operations that await it, and ``run_prepare``) are async, ready for an
asyncssh-based implementation.
"""

from .commands import (
    AptInstallCommand,
    AptRemoveCommand,
    Command,
    LinkCommand,
    StringCommand,
)
from .core import FactCache, HostRuntime, OpContext, OperationRequest, op
from .drivers import (
    ApplyReport,
    DeterminismError,
    LogEntry,
    PlannedOperation,
    format_log,
    format_plan,
    run,
    run_apply,
    run_prepare,
    send_command,
)
from .facts import AptPackages, FileLink
from .hosts import SimulatedHost
from .operations import apt, files


def hello() -> str:
    return "Hello from reemote5!"


__all__ = [
    "ApplyReport",
    "AptInstallCommand",
    "AptPackages",
    "AptRemoveCommand",
    "Command",
    "DeterminismError",
    "FactCache",
    "FileLink",
    "HostRuntime",
    "LinkCommand",
    "LogEntry",
    "OpContext",
    "OperationRequest",
    "PlannedOperation",
    "SimulatedHost",
    "StringCommand",
    "apt",
    "files",
    "format_log",
    "format_plan",
    "hello",
    "op",
    "run",
    "run_apply",
    "run_prepare",
    "send_command",
]
