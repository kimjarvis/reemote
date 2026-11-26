# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""The ``uv`` module namespace.

Re-exports everything from ``uv.py`` so the fact (``uv.Packages``, returning
its ``uv.PackageInfo`` entry type) is reachable through the package name
alone. pyinfra's ``uv.packages`` operation is not ported yet, so the
namespace currently ships the fact alone.
"""

from . import uv
from .uv import PackageInfo, Packages

__all__ = [
    "PackageInfo",
    "Packages",
    "uv",
]
