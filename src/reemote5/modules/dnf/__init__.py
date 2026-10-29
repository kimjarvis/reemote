"""The ``dnf`` module namespace.

Re-exports everything from ``dnf.py`` so both the operations (``dnf.packages``,
``dnf.repo``, ``dnf.rpm``) and ``dnf.DnfPackages`` & friends (the facts) are
reachable through the package name alone.
"""

from . import dnf
from .dnf import (
    DnfAvailableGroups,
    DnfCleanCommand,
    DnfDisabledModules,
    DnfEnabledModules,
    DnfGroups,
    DnfInstallCommand,
    DnfInstalledModules,
    DnfPackages,
    DnfRemoveCommand,
    DnfRepoCommand,
    DnfRepoFile,
    DnfRepoList,
    DnfRepositories,
    DnfUpdateCommand,
    RpmInstallCommand,
    RpmPackage,
    packages,
    repo,
    rpm,
)

__all__ = [
    "DnfAvailableGroups",
    "DnfCleanCommand",
    "DnfDisabledModules",
    "DnfEnabledModules",
    "DnfGroups",
    "DnfInstallCommand",
    "DnfInstalledModules",
    "DnfPackages",
    "DnfRemoveCommand",
    "DnfRepoCommand",
    "DnfRepoFile",
    "DnfRepoList",
    "DnfRepositories",
    "DnfUpdateCommand",
    "RpmInstallCommand",
    "RpmPackage",
    "dnf",
    "packages",
    "repo",
    "rpm",
]
