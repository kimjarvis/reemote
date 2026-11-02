# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""The ``apk`` module namespace.

Re-exports everything from ``apk.py`` so the facts (``apk.ApkPackages``,
``apk.ApkExplicitPackages``, ``apk.ApkPackage`` with its ``apk.ApkPackageInfo``
entry type, ``apk.ApkRepositories``) are reachable through the package name
alone. The namespace is fact-only so far: pyinfra's ``apk`` operations are
not migrated yet, so there are no operations or command descriptors to
re-export.
"""

from . import apk
from .apk import (
    ApkExplicitPackages,
    ApkPackage,
    ApkPackageInfo,
    ApkPackages,
    ApkRepositories,
)

__all__ = [
    "ApkExplicitPackages",
    "ApkPackage",
    "ApkPackageInfo",
    "ApkPackages",
    "ApkRepositories",
    "apk",
]
