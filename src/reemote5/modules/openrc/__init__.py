# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""The ``openrc`` module namespace.

Re-exports everything from ``openrc.py`` so the facts
(``openrc.OpenrcEnabled``, ``openrc.OpenrcRunlevels``,
``openrc.OpenrcServices``, ``openrc.OpenrcStatus``, ``openrc.OpenrcVersion``),
the ``openrc.service``, ``openrc.runlevel`` and ``openrc.custom_runlevel``
operations and their typed command descriptors (``openrc.RcServiceCommand``,
``openrc.RcUpdateCommand``, ``openrc.RunlevelCommand``) are reachable through
the package name alone.
"""

from . import openrc
from .openrc import (
    OpenrcEnabled,
    OpenrcRunlevels,
    OpenrcServices,
    OpenrcStatus,
    OpenrcVersion,
    RcServiceCommand,
    RcUpdateCommand,
    RunlevelCommand,
    custom_runlevel,
    runlevel,
    service,
)

__all__ = [
    "OpenrcEnabled",
    "OpenrcRunlevels",
    "OpenrcServices",
    "OpenrcStatus",
    "OpenrcVersion",
    "RcServiceCommand",
    "RcUpdateCommand",
    "RunlevelCommand",
    "custom_runlevel",
    "openrc",
    "runlevel",
    "service",
]