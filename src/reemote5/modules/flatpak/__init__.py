# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""The ``flatpak`` module namespace.

Re-exports everything from ``flatpak.py`` so the facts (``flatpak.FlatpakPackage``,
returning its ``flatpak.FlatpakPackageInfo`` entry type, and
``flatpak.FlatpakPackages``, the bare installed-flatpak application-ID list) and
the ``flatpak.packages`` operation with its typed command descriptors
(``flatpak.FlatpakInstallCommand``, ``flatpak.FlatpakRemoveCommand``) are
reachable through the package name alone.
"""

from . import flatpak
from .flatpak import (
    FlatpakInstallCommand,
    FlatpakPackage,
    FlatpakPackageInfo,
    FlatpakPackages,
    FlatpakRemoveCommand,
    packages,
)

__all__ = [
    "FlatpakInstallCommand",
    "FlatpakPackage",
    "FlatpakPackageInfo",
    "FlatpakPackages",
    "FlatpakRemoveCommand",
    "flatpak",
    "packages",
]
