# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""The ``apk`` module namespace.

Re-exports everything from ``apk.py`` so the facts (``apk.ApkAutoremovable``,
``apk.ApkPackages``, ``apk.ApkExplicitPackages``, ``apk.ApkHeldPackages``,
``apk.ApkPackage`` with its ``apk.ApkPackageInfo`` entry type,
``apk.ApkRepositories``), the ``apk.autoremove``, ``apk.packages``,
``apk.virtual`` and ``apk.hold`` operations and their typed command
descriptors (``apk.ApkInstallCommand``, ``apk.ApkPurgeCommand``,
``apk.ApkRemoveCommand``, ``apk.ApkUpgradeCommand``,
``apk.ApkVirtualCommand``, ``apk.ApkHoldCommand``) are reachable through the
package name alone.
"""

from . import apk
from .apk import (
    ApkAutoremovable,
    ApkExplicitPackages,
    ApkHeldPackages,
    ApkHoldCommand,
    ApkInstallCommand,
    ApkPackage,
    ApkPackageInfo,
    ApkPackages,
    ApkPurgeCommand,
    ApkRemoveCommand,
    ApkRepositories,
    ApkUpgradeCommand,
    ApkVirtualCommand,
    autoremove,
    hold,
    packages,
    virtual,
)

__all__ = [
    "ApkAutoremovable",
    "ApkExplicitPackages",
    "ApkHeldPackages",
    "ApkHoldCommand",
    "ApkInstallCommand",
    "ApkPackage",
    "ApkPackageInfo",
    "ApkPackages",
    "ApkPurgeCommand",
    "ApkRemoveCommand",
    "ApkRepositories",
    "ApkUpgradeCommand",
    "ApkVirtualCommand",
    "apk",
    "autoremove",
    "hold",
    "packages",
    "virtual",
]
