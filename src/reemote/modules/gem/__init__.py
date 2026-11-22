# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""The ``gem`` module namespace.

Re-exports everything from ``gem.py`` so the fact (``gem.GemPackages``,
returning its ``gem.GemPackageInfo`` entry type), the ``gem.packages``
operation and its typed command descriptors (``gem.GemInstallCommand``,
``gem.GemUpdateCommand``, ``gem.GemRemoveCommand``) are reachable through
the package name alone.
"""

from . import gem
from .gem import (
    GemInstallCommand,
    GemPackageInfo,
    GemPackages,
    GemRemoveCommand,
    GemUpdateCommand,
    packages,
)

__all__ = [
    "GemInstallCommand",
    "GemPackageInfo",
    "GemPackages",
    "GemRemoveCommand",
    "GemUpdateCommand",
    "gem",
    "packages",
]
