"""The ``dnf`` module namespace.

Re-exports everything from ``dnf.py`` so both ``dnf.packages`` (the
operation) and ``dnf.DnfPackages`` & friends (the facts) are reachable
through the package name alone.
"""

from . import dnf
from .dnf import (
    DnfCleanCommand,
    DnfDisabledModules,
    DnfEnabledModules,
    DnfInstallCommand,
    DnfPackages,
    DnfRemoveCommand,
    DnfRepositories,
    DnfUpdateCommand,
    packages,
)

__all__ = [
    "DnfCleanCommand",
    "DnfDisabledModules",
    "DnfEnabledModules",
    "DnfInstallCommand",
    "DnfPackages",
    "DnfRemoveCommand",
    "DnfRepositories",
    "DnfUpdateCommand",
    "dnf",
    "packages",
]
