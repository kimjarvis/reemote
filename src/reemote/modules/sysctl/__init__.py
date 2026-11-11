# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""The ``sysctl`` module namespace.

Re-exports everything from ``sysctl.py`` so the facts (the relocated
``sysctl.Sysctl`` runtime dump and the originals ``sysctl.SysctlFile`` /
``sysctl.SysctlFiles`` persistence reads), the ``sysctl.setting`` and
``sysctl.conf`` operations and the typed command descriptors
(``SysctlSetCommand``, ``SysctlFileUploadCommand`` /
``SysctlFileInstallCommand`` / ``SysctlFileRemoveCommand`` and
``SysctlReloadCommand``) are reachable through the package name alone.
This namespace is the canonical home for everything sysctl: ``server`` no
longer exports a ``Sysctl`` fact, and the top-level ``reemote.Sysctl``
binding points here.
"""

from . import sysctl
from .sysctl import (
    Sysctl,
    SysctlFile,
    SysctlFileInstallCommand,
    SysctlFileRemoveCommand,
    SysctlFiles,
    SysctlFileUploadCommand,
    SysctlReloadCommand,
    SysctlSetCommand,
    conf,
    setting,
)

__all__ = [
    "Sysctl",
    "SysctlFile",
    "SysctlFileInstallCommand",
    "SysctlFileRemoveCommand",
    "SysctlFileUploadCommand",
    "SysctlFiles",
    "SysctlReloadCommand",
    "SysctlSetCommand",
    "conf",
    "setting",
    "sysctl",
]