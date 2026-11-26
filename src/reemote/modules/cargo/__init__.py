# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""The ``cargo`` module namespace.

Re-exports everything from ``cargo.py`` so the fact (``cargo.CargoPackages``
with its ``cargo.CargoPackageInfo`` entry type) and the operation
(``cargo.packages`` with its ``cargo.CargoInstallCommand``/
``cargo.CargoRemoveCommand`` descriptors) are reachable through the package
name alone.
"""

from . import cargo
from .cargo import (
    CargoInstallCommand,
    CargoPackageInfo,
    CargoPackages,
    CargoRemoveCommand,
    packages,
)

__all__ = [
    "CargoInstallCommand",
    "CargoPackageInfo",
    "CargoPackages",
    "CargoRemoveCommand",
    "cargo",
    "packages",
]
