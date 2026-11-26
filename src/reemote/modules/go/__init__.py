# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""The ``go`` module namespace.

Re-exports everything from ``go.py`` so the facts (``go.GoPackages`` with its
``go.GoPackageInfo`` entry type, ``go.GoVersion`` with its
``go.GoVersionInfo`` entry, ``go.GoEnv``, ``go.GoInstalledSDKs``,
``go.GoModuleInfo`` with its ``go.GoModInfo`` entry, and
``go.GoBinaryBuildInfo`` with its ``go.GoBuildInfo`` entry) and the
``go.packages`` operation (with its ``go.GoInstallCommand``/
``go.GoRemoveCommand`` descriptors) are reachable through the package name
alone.
"""

from . import go
from .go import (
    GoBinaryBuildInfo,
    GoBuildInfo,
    GoEnv,
    GoInstallCommand,
    GoInstalledSDKs,
    GoModInfo,
    GoModuleInfo,
    GoPackageInfo,
    GoPackages,
    GoRemoveCommand,
    GoVersion,
    GoVersionInfo,
    packages,
)

__all__ = [
    "GoBinaryBuildInfo",
    "GoBuildInfo",
    "GoEnv",
    "GoInstallCommand",
    "GoInstalledSDKs",
    "GoModInfo",
    "GoModuleInfo",
    "GoPackageInfo",
    "GoPackages",
    "GoRemoveCommand",
    "GoVersion",
    "GoVersionInfo",
    "go",
    "packages",
]
