# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""The ``snap`` module namespace.

Re-exports everything from ``snap.py`` so the facts
(``snap.SnapPackage``, returning its ``snap.SnapPackageInfo`` entry type, and
``snap.SnapPackages``, the bare installed-snap name list), the
``snap.package`` operation and its typed command descriptors
(``snap.SnapInstallCommand``, ``snap.SnapRefreshCommand``,
``snap.SnapRemoveCommand``) are reachable through the package name alone.
"""

from . import snap
from .snap import (
    SnapInstallCommand,
    SnapPackage,
    SnapPackageInfo,
    SnapPackages,
    SnapRefreshCommand,
    SnapRemoveCommand,
    package,
)

__all__ = [
    "SnapInstallCommand",
    "SnapPackage",
    "SnapPackageInfo",
    "SnapPackages",
    "SnapRefreshCommand",
    "SnapRemoveCommand",
    "package",
    "snap",
]
