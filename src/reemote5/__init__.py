"""reemote5: a POC model for generator-based two-phase execution.

An alternative to pyinfra's prepare/execute design in which deploy scripts
are generators yielding ``OperationRequest`` descriptors, and the driver
re-invokes the generator per phase, advancing all hosts in lockstep with a
fresh ``FactCache`` per step. The command-execution path (``send_command``
and the apply drivers) and fact gathering (each fact's ``process``,
``FactCache.get``, the operations that await it, and ``run_prepare``) are
async, and a plain ``Host`` runs commands and gathers facts over a real
asyncssh transport built from its inventory ``connection``/``session`` data.
"""

from .commands import Command, StringCommand
from .core import FactCache, HostRuntime, OpContext, OperationRequest, op
from .drivers import (
    ApplyReport,
    DeterminismError,
    LogEntry,
    PlannedOperation,
    command_shell,
    format_log,
    format_plan,
    run,
    run_apply,
    run_prepare,
    send_command,
)
from .facts import DnfPackages, FileExists, FileLink
from .hosts import Host
from .operations import dnf, files
from .operations.dnf import DnfInstallCommand, DnfRemoveCommand
from .operations.files import FileCommand, LinkCommand


def hello() -> str:
    return "Hello from reemote5!"


__all__ = [
    "ApplyReport",
    "Command",
    "DeterminismError",
    "DnfInstallCommand",
    "DnfPackages",
    "DnfRemoveCommand",
    "FactCache",
    "FileCommand",
    "FileExists",
    "FileLink",
    "Host",
    "HostRuntime",
    "LinkCommand",
    "LogEntry",
    "OpContext",
    "OperationRequest",
    "PlannedOperation",
    "StringCommand",
    "command_shell",
    "dnf",
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
