# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""The ``openrc`` module namespace.

Re-exports everything from ``openrc.py`` so the facts
(``openrc.OpenrcConfigFile``, ``openrc.OpenrcEnabled``,
``openrc.OpenrcRunlevels``, ``openrc.OpenrcServices``,
``openrc.OpenrcStatus``, ``openrc.OpenrcVersion``), the ``openrc.service``,
``openrc.runlevel``, ``openrc.custom_runlevel`` and ``openrc.config``
operations and their typed command descriptors (``openrc.RcServiceCommand``,
``openrc.RcUpdateCommand``, ``openrc.RunlevelCommand``,
``openrc.OpenrcConfigUploadCommand``, ``openrc.OpenrcConfigInstallCommand``)
are reachable through the package name alone.
"""

from . import openrc
from .openrc import (
    OpenrcConfigFile,
    OpenrcConfigInstallCommand,
    OpenrcConfigUploadCommand,
    OpenrcEnabled,
    OpenrcRunlevels,
    OpenrcServices,
    OpenrcStatus,
    OpenrcVersion,
    RcServiceCommand,
    RcUpdateCommand,
    RunlevelCommand,
    config,
    custom_runlevel,
    runlevel,
    service,
)

__all__ = [
    "OpenrcConfigFile",
    "OpenrcConfigInstallCommand",
    "OpenrcConfigUploadCommand",
    "OpenrcEnabled",
    "OpenrcRunlevels",
    "OpenrcServices",
    "OpenrcStatus",
    "OpenrcVersion",
    "RcServiceCommand",
    "RcUpdateCommand",
    "RunlevelCommand",
    "config",
    "custom_runlevel",
    "openrc",
    "runlevel",
    "service",
]