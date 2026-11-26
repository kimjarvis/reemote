# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""The ``go`` module namespace.

Re-exports everything from ``go.py`` so the fact (``go.GoPackages``,
returning its ``go.GoPackageInfo`` entry type) and the ``go.packages``
operation (with its ``go.GoInstallCommand``/``go.GoRemoveCommand``
descriptors) are reachable through the package name alone.
"""

from . import go
from .go import (
    GoInstallCommand,
    GoPackageInfo,
    GoPackages,
    GoRemoveCommand,
    packages,
)

__all__ = [
    "GoInstallCommand",
    "GoPackageInfo",
    "GoPackages",
    "GoRemoveCommand",
    "go",
    "packages",
]
