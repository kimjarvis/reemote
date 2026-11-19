# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""The ``pip`` module namespace.

Re-exports everything from ``pip.py`` so the fact (``pip.PipPackages``,
returning its ``pip.PipPackageInfo`` entry type), the ``pip.packages``
operation and the ``pip.virtualenv``/``pip.venv`` virtualenv operations, with
their typed command descriptors
(``pip.PipInstallCommand``, ``pip.PipUpgradeCommand``,
``pip.PipRemoveCommand``, ``pip.PipRequirementsCommand``,
``pip.PipVenvCreateCommand``, ``pip.PipVenvRemoveCommand``) are reachable
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
    PipVenvCreateCommand,
    PipVenvRemoveCommand,
    packages,
    venv,
    virtualenv,
)

__all__ = [
    "PipInstallCommand",
    "PipPackageInfo",
    "PipPackages",
    "PipRemoveCommand",
    "PipRequirementsCommand",
    "PipUpgradeCommand",
    "PipVenvCreateCommand",
    "PipVenvRemoveCommand",
    "packages",
    "pip",
    "venv",
    "virtualenv",
]
