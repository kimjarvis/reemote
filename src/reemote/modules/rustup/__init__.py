# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""The ``rustup`` module namespace.

Re-exports everything from ``rustup.py`` so the four facts (``rustup.RustupToolchains``
with its ``rustup.RustupToolchainInfo`` entry type, ``rustup.RustupDefaultToolchain``,
``rustup.RustupTargets`` and ``rustup.RustupComponents``) and the three
operations (``rustup.toolchains``, ``rustup.targets`` and ``rustup.components``
with their ``Rustup*Command`` descriptors) are reachable through the package
name alone.
"""

from . import rustup
from .rustup import (
    RustupComponentAddCommand,
    RustupComponentRemoveCommand,
    RustupComponents,
    RustupDefaultCommand,
    RustupDefaultToolchain,
    RustupTargetAddCommand,
    RustupTargetRemoveCommand,
    RustupTargets,
    RustupToolchainInfo,
    RustupToolchainInstallCommand,
    RustupToolchainRemoveCommand,
    RustupToolchains,
    components,
    targets,
    toolchains,
)

__all__ = [
    "RustupComponentAddCommand",
    "RustupComponentRemoveCommand",
    "RustupComponents",
    "RustupDefaultCommand",
    "RustupDefaultToolchain",
    "RustupTargetAddCommand",
    "RustupTargetRemoveCommand",
    "RustupTargets",
    "RustupToolchainInfo",
    "RustupToolchainInstallCommand",
    "RustupToolchainRemoveCommand",
    "RustupToolchains",
    "components",
    "rustup",
    "targets",
    "toolchains",
]
