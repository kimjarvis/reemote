# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""The ``uv`` module namespace.

Re-exports everything from ``uv.py`` so the facts -- ``uv.Info`` (returning its
``uv.InstallInfo`` record), ``uv.Packages`` (with its ``uv.PackageInfo`` entry
type), ``uv.Pythons`` (with ``uv.PythonInfo``) and ``uv.Tools`` (with
``uv.ToolInfo``) -- are reachable through the package name alone. pyinfra's
``uv.packages`` operation is not ported yet, so the namespace currently ships
the four facts alone.
"""

from . import uv
from .uv import (
    Info,
    InstallInfo,
    PackageInfo,
    Packages,
    PythonInfo,
    Pythons,
    ToolInfo,
    Tools,
)

__all__ = [
    "Info",
    "InstallInfo",
    "PackageInfo",
    "Packages",
    "PythonInfo",
    "Pythons",
    "ToolInfo",
    "Tools",
    "uv",
]
