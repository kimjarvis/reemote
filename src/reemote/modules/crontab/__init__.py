# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""The ``crontab`` module namespace.

Re-exports everything from ``crontab.py`` so the ``Crontab`` whole-table fact,
its structured ``CrontabEntries`` companion (with the ``CrontabEntry`` entry
shape), the ``crontab`` operation and the typed command descriptors
(``CrontabUploadCommand`` / ``CrontabInstallCommand``) are reachable through
the package name alone. This namespace is the canonical home for everything
crontab.
"""

from .crontab import (
    Crontab,
    CrontabEntries,
    CrontabEntry,
    CrontabInstallCommand,
    CrontabUploadCommand,
    crontab,
)

__all__ = [
    "Crontab",
    "CrontabEntries",
    "CrontabEntry",
    "CrontabInstallCommand",
    "CrontabUploadCommand",
    "crontab",
]
