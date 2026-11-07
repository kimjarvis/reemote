# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""The ``systemd`` module namespace.

Re-exports everything from ``systemd.py`` so the facts (the per-unit
``systemd.SystemdServiceState``/``systemd.SystemdServiceEnabled`` probes, the
fleet-wide ``systemd.SystemdStatus``/``systemd.SystemdEnabled`` ports, and the
originals ``SystemdUnitProperties``, ``SystemdFailedUnits``, ``SystemdTimers``,
``SystemdUnitFiles``, ``SystemdVersion``, ``SystemdSystemState`` and
``SystemdDependencies``, with their entry TypedDicts), the ``systemd.service``
and ``systemd.daemon_reload`` operations plus the originals ``mask``,
``reset_failed``, ``dropin``, ``tmpfiles``, ``sysusers`` and ``preset``, the
typed command descriptors (``systemd.SystemctlCommand`` and the file/apply
``SystemdFileUploadCommand``/``SystemdFileInstallCommand``/
``SystemdFileRemoveCommand``/``SystemdTmpfilesCommand``/``SystemdSysusersCommand``)
and the ``unit_name`` normalization helper are reachable through the package
name alone.
"""

from . import systemd
from .systemd import (
    SystemctlCommand,
    SystemdDependencies,
    SystemdEnabled,
    SystemdFailedUnit,
    SystemdFailedUnits,
    SystemdFileInstallCommand,
    SystemdFileRemoveCommand,
    SystemdFileUploadCommand,
    SystemdServiceEnabled,
    SystemdServiceState,
    SystemdStatus,
    SystemdSystemState,
    SystemdSysusersCommand,
    SystemdTimer,
    SystemdTimers,
    SystemdTmpfilesCommand,
    SystemdUnitFile,
    SystemdUnitFiles,
    SystemdUnitProperties,
    SystemdVersion,
    SystemdVersionInfo,
    daemon_reload,
    dropin,
    mask,
    preset,
    reset_failed,
    service,
    sysusers,
    tmpfiles,
    unit_name,
)

__all__ = [
    "SystemctlCommand",
    "SystemdDependencies",
    "SystemdEnabled",
    "SystemdFailedUnit",
    "SystemdFailedUnits",
    "SystemdFileInstallCommand",
    "SystemdFileRemoveCommand",
    "SystemdFileUploadCommand",
    "SystemdServiceEnabled",
    "SystemdServiceState",
    "SystemdStatus",
    "SystemdSystemState",
    "SystemdSysusersCommand",
    "SystemdTimer",
    "SystemdTimers",
    "SystemdTmpfilesCommand",
    "SystemdUnitFile",
    "SystemdUnitFiles",
    "SystemdUnitProperties",
    "SystemdVersion",
    "SystemdVersionInfo",
    "daemon_reload",
    "dropin",
    "mask",
    "preset",
    "reset_failed",
    "service",
    "systemd",
    "sysusers",
    "tmpfiles",
    "unit_name",
]