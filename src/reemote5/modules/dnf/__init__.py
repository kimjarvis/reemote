"""The ``dnf`` module namespace.

Re-exports everything from ``dnf.py`` so both the operations (``dnf.packages``,
``dnf.update``, ``dnf.repo``, ``dnf.rpm``, ``dnf.key`` and ``dnf.module``) and
``dnf.DnfPackages`` & friends (the facts) are reachable through the package
name alone.
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
    DnfKeyImportCommand,
    DnfKeyUploadCommand,
    DnfModuleDisableCommand,
    DnfModuleEnableCommand,
    DnfPackages,
    DnfRemoveCommand,
    DnfRepoCommand,
    DnfRepoFile,
    DnfRepoList,
    DnfRepositories,
    DnfUpdateCommand,
    RpmInstallCommand,
    RpmPackage,
    key,
    module,
    packages,
    repo,
    rpm,
    update,
)

__all__ = [
    "DnfAvailableGroups",
    "DnfCleanCommand",
    "DnfDisabledModules",
    "DnfEnabledModules",
    "DnfGroups",
    "DnfInstallCommand",
    "DnfInstalledModules",
    "DnfKeyImportCommand",
    "DnfKeyUploadCommand",
    "DnfModuleDisableCommand",
    "DnfModuleEnableCommand",
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
    "key",
    "module",
    "packages",
    "repo",
    "rpm",
    "update",
]
