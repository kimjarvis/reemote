# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""The ``pip`` module namespace.

Re-exports everything from ``pip.py`` so the fact (``pip.PipPackages``,
returning its ``pip.PipPackageInfo`` entry type) and the ``pip.packages``
operation with its typed command descriptors
(``pip.PipInstallCommand``, ``pip.PipUpgradeCommand``,
``pip.PipRemoveCommand``, ``pip.PipRequirementsCommand``) are reachable
through the package name alone.
"""

from . import pip
from .pip import (
    PipInstallCommand,
    PipPackageInfo,
    PipPackages,
    PipRemoveCommand,
    PipRequirementsCommand,
    PipUpgradeCommand,
    packages,
)

__all__ = [
    "PipInstallCommand",
    "PipPackageInfo",
    "PipPackages",
    "PipRemoveCommand",
    "PipRequirementsCommand",
    "PipUpgradeCommand",
    "packages",
    "pip",
]
