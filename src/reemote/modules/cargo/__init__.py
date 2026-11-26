# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""The ``cargo`` module namespace.

Re-exports everything from ``cargo.py`` so the facts (``cargo.CargoPackages``
with its ``cargo.CargoPackageInfo`` entry type, and ``cargo.CargoConfig``)
and the operations (``cargo.packages`` with its ``cargo.CargoInstallCommand``/
``cargo.CargoRemoveCommand`` descriptors, ``cargo.cache_clean`` with its
``cargo.CargoCleanCommand``/``cargo.CargoCacheRemoveCommand`` descriptors) are
reachable through the package name alone.
"""

from . import cargo
from .cargo import (
    CargoCacheRemoveCommand,
    CargoCleanCommand,
    CargoConfig,
    CargoInstallCommand,
    CargoPackageInfo,
    CargoPackages,
    CargoRemoveCommand,
    cache_clean,
    packages,
)

__all__ = [
    "CargoCacheRemoveCommand",
    "CargoCleanCommand",
    "CargoConfig",
    "CargoInstallCommand",
    "CargoPackageInfo",
    "CargoPackages",
    "CargoRemoveCommand",
    "cache_clean",
    "cargo",
    "packages",
]
