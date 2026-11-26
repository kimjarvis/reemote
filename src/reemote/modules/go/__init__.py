# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""The ``go`` module namespace.

Re-exports everything from ``go.py`` so the fact (``go.GoPackages``,
returning its ``go.GoPackageInfo`` entry type) is reachable through the
package name alone.
"""

from . import go
from .go import GoPackageInfo, GoPackages

__all__ = [
    "GoPackageInfo",
    "GoPackages",
    "go",
]
