# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""The ``go`` module namespace.

Re-exports everything from ``go.py`` so the facts (``go.GoPackages`` with its
``go.GoPackageInfo`` entry type, ``go.GoVersion`` with its
``go.GoVersionInfo`` entry, ``go.GoEnv``, ``go.GoInstalledSDKs``,
``go.GoModuleInfo`` with its ``go.GoModInfo`` entry, and
``go.GoBinaryBuildInfo`` with its ``go.GoBuildInfo`` entry) and the
operations (``go.packages`` with its ``go.GoInstallCommand``/
``go.GoRemoveCommand`` descriptors, ``go.env`` with its
``go.GoEnvSetCommand``/``go.GoEnvUnsetCommand`` descriptors,
``go.cache_clean`` with its ``go.GoCleanCommand`` descriptor and ``go.build``
with its ``go.GoBuildCommand`` descriptor) are reachable through the package
name alone.
"""

from . import go
from .go import (
    GoBinaryBuildInfo,
    GoBuildCommand,
    GoBuildInfo,
    GoCleanCommand,
    GoEnv,
    GoEnvSetCommand,
    GoEnvUnsetCommand,
    GoInstallCommand,
    GoInstalledSDKs,
    GoModInfo,
    GoModuleInfo,
    GoPackageInfo,
    GoPackages,
    GoRemoveCommand,
    GoVersion,
    GoVersionInfo,
    build,
    cache_clean,
    env,
    packages,
)

__all__ = [
    "GoBinaryBuildInfo",
    "GoBuildCommand",
    "GoBuildInfo",
    "GoCleanCommand",
    "GoEnv",
    "GoEnvSetCommand",
    "GoEnvUnsetCommand",
    "GoInstallCommand",
    "GoInstalledSDKs",
    "GoModInfo",
    "GoModuleInfo",
    "GoPackageInfo",
    "GoPackages",
    "GoRemoveCommand",
    "GoVersion",
    "GoVersionInfo",
    "build",
    "cache_clean",
    "env",
    "go",
    "packages",
]
