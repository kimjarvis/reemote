# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""The ``uv`` module namespace.

Re-exports everything from ``uv.py`` so the facts -- ``uv.Info`` (returning its
``uv.InstallInfo`` record), ``uv.Packages`` (with its ``uv.PackageInfo`` entry
type), ``uv.Pythons`` (with ``uv.PythonInfo``) and ``uv.Tools`` (with
``uv.ToolInfo``) -- and the six pyinfra operations -- ``uv.packages``,
``uv.pythons``, ``uv.tools``, ``uv.tools_upgrade_all``, ``uv.tools_update_shell``
and ``uv.venv`` -- plus their command descriptors are reachable through the
package name alone.
"""

from . import uv
from .uv import (
    Info,
    InstallInfo,
    PackageInfo,
    Packages,
    PythonInfo,
    Pythons,
    ToolInfo,
    Tools,
    UvPipInstallCommand,
    UvPipRemoveCommand,
    UvPipRequirementsCommand,
    UvPipUpgradeCommand,
    UvPythonInstallCommand,
    UvPythonRemoveCommand,
    UvToolInstallCommand,
    UvToolRemoveCommand,
    UvToolUpdateShellCommand,
    UvToolUpgradeAllCommand,
    UvToolUpgradeCommand,
    UvVenvCreateCommand,
    UvVenvRemoveCommand,
    packages,
    pythons,
    tools,
    tools_update_shell,
    tools_upgrade_all,
    venv,
)

__all__ = [
    "Info",
    "InstallInfo",
    "PackageInfo",
    "Packages",
    "PythonInfo",
    "Pythons",
    "ToolInfo",
    "Tools",
    "UvPipInstallCommand",
    "UvPipRemoveCommand",
    "UvPipRequirementsCommand",
    "UvPipUpgradeCommand",
    "UvPythonInstallCommand",
    "UvPythonRemoveCommand",
    "UvToolInstallCommand",
    "UvToolRemoveCommand",
    "UvToolUpdateShellCommand",
    "UvToolUpgradeAllCommand",
    "UvToolUpgradeCommand",
    "UvVenvCreateCommand",
    "UvVenvRemoveCommand",
    "packages",
    "pythons",
    "tools",
    "tools_update_shell",
    "tools_upgrade_all",
    "uv",
    "venv",
]
