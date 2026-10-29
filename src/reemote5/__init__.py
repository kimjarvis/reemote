"""reemote5: a POC model for generator-based two-phase execution.

An alternative to pyinfra's prepare/execute design in which deploy scripts
are generators yielding ``OperationRequest`` descriptors, and the driver
re-invokes the generator per phase, advancing all hosts in lockstep with a
fresh ``FactCache`` per step. The command-execution path (``send_command``
and the apply drivers) and fact gathering (each fact's ``process``,
``FactCache.get``, the operations that await it, and ``run_prepare``) are
async, and a plain ``Host`` runs commands and gathers facts over a real
asyncssh transport built from its inventory ``connection`` data.
"""

from .commands import (
    Command,
    StringCommand,
    TransferCommand,
    WaitCommand,
    WaitTimeoutError,
)
from .core import (
    FactCache,
    HostRuntime,
    OpContext,
    OperationRequest,
    Privilege,
    compose,
    op,
    sub_deploy,
)
from .drivers import (
    ApplyReport,
    DeterminismError,
    LogEntry,
    PlannedOperation,
    command_shell,
    format_log,
    format_plan,
    gather_fact,
    run,
    run_apply,
    run_prepare,
    send_command,
)
from .hosts import (
    CommandError,
    Host,
    HostConnectionError,
    InventoryEntry,
    InventoryError,
    PrivilegeError,
    validate_inventory,
)
from .modules import dnf, files, server
from .modules.dnf.dnf import (
    DnfCleanCommand,
    DnfDisabledModules,
    DnfEnabledModules,
    DnfInstallCommand,
    DnfPackages,
    DnfRemoveCommand,
    DnfRepositories,
    DnfUpdateCommand,
)
from .modules.files.files import (
    FileCommand,
    FileExists,
    FileLink,
    GetCommand,
    LinkCommand,
    LocalSha1,
    PutCommand,
    RemoteFileSha1,
)
from .modules.server.server import (
    SystemctlCommand,
    SystemdServiceEnabled,
    SystemdServiceState,
    WaitForFactCommand,
)


def hello() -> str:
    return "Hello from reemote5!"


__all__ = [
    "ApplyReport",
    "Command",
    "CommandError",
    "DeterminismError",
    "DnfCleanCommand",
    "DnfDisabledModules",
    "DnfEnabledModules",
    "DnfInstallCommand",
    "DnfPackages",
    "DnfRemoveCommand",
    "DnfRepositories",
    "DnfUpdateCommand",
    "FactCache",
    "FileCommand",
    "FileExists",
    "FileLink",
    "GetCommand",
    "Host",
    "HostConnectionError",
    "HostRuntime",
    "InventoryEntry",
    "InventoryError",
    "LinkCommand",
    "LocalSha1",
    "LogEntry",
    "OpContext",
    "OperationRequest",
    "PlannedOperation",
    "Privilege",
    "PrivilegeError",
    "PutCommand",
    "RemoteFileSha1",
    "StringCommand",
    "SystemctlCommand",
    "SystemdServiceEnabled",
    "SystemdServiceState",
    "TransferCommand",
    "WaitCommand",
    "WaitForFactCommand",
    "WaitTimeoutError",
    "command_shell",
    "compose",
    "dnf",
    "files",
    "format_log",
    "format_plan",
    "gather_fact",
    "hello",
    "op",
    "run",
    "run_apply",
    "run_prepare",
    "send_command",
    "server",
    "sub_deploy",
    "validate_inventory",
]
