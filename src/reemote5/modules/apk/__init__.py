# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""The ``apk`` module namespace.

Re-exports everything from ``apk.py`` so the facts (``apk.ApkAutoremovablePackages``,
``apk.ApkExplicitPackages``, ``apk.ApkHeldPackages`` and
``apk.ApkInstalledPackages``, all returning lists of the shared
``apk.ApkPackageVersion`` entry type,
``apk.ApkPackage`` with its ``apk.ApkPackageInfo`` entry type,
``apk.ApkRepositories`` with its ``apk.ApkRepository`` entry type), the
``apk.autoremove``, ``apk.packages``,
``apk.virtual`` and ``apk.hold`` operations and their typed command
descriptors (``apk.ApkInstallCommand``, ``apk.ApkPurgeCommand``,
``apk.ApkRemoveCommand``, ``apk.ApkUpgradeCommand``,
``apk.ApkVirtualCommand``, ``apk.ApkHoldCommand``) are reachable through the
package name alone.
"""

from . import apk
from .apk import (
    ApkAutoremovablePackages,
    ApkExplicitPackages,
    ApkHeldPackages,
    ApkHoldCommand,
    ApkInstallCommand,
    ApkInstalledPackages,
    ApkPackage,
    ApkPackageInfo,
    ApkPackageVersion,
    ApkPurgeCommand,
    ApkRemoveCommand,
    ApkRepositories,
    ApkRepository,
    ApkUpgradeCommand,
    ApkVirtualCommand,
    autoremove,
    hold,
    packages,
    virtual,
)

__all__ = [
    "ApkAutoremovablePackages",
    "ApkExplicitPackages",
    "ApkHeldPackages",
    "ApkHoldCommand",
    "ApkInstallCommand",
    "ApkInstalledPackages",
    "ApkPackage",
    "ApkPackageInfo",
    "ApkPackageVersion",
    "ApkPurgeCommand",
    "ApkRemoveCommand",
    "ApkRepositories",
    "ApkRepository",
    "ApkUpgradeCommand",
    "ApkVirtualCommand",
    "apk",
    "autoremove",
    "hold",
    "packages",
    "virtual",
]
