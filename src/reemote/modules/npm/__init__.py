# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""The ``npm`` module namespace.

Re-exports everything from ``npm.py`` so the fact (``npm.NpmPackages``,
returning its ``npm.NpmPackageInfo`` entry type), the ``npm.packages``
operation and its typed command descriptors
(``npm.NpmInstallCommand``, ``npm.NpmUpdateCommand``,
``npm.NpmRemoveCommand``, ``npm.NpmPackageJsonCommand``) are reachable
through the package name alone.
"""

from . import npm
from .npm import (
    NpmInstallCommand,
    NpmPackageInfo,
    NpmPackageJsonCommand,
    NpmPackages,
    NpmRemoveCommand,
    NpmUpdateCommand,
    packages,
)

__all__ = [
    "NpmInstallCommand",
    "NpmPackageInfo",
    "NpmPackageJsonCommand",
    "NpmPackages",
    "NpmRemoveCommand",
    "NpmUpdateCommand",
    "npm",
    "packages",
]
