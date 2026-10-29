"""The ``server`` module namespace.

Re-exports everything from ``server.py`` so the operations
(``server.service``, ``server.wait_service``), the facts
(``server.SystemdServiceState``, ``server.SystemdServiceEnabled``) and the
descriptors (``server.SystemctlCommand``, ``server.WaitForFactCommand``) are
reachable through the package name alone.
"""

from . import server
from .server import (
    SystemctlCommand,
    SystemdServiceEnabled,
    SystemdServiceState,
    WaitForFactCommand,
    is_active,
    service,
    unit_name,
    wait_service,
)

__all__ = [
    "SystemctlCommand",
    "SystemdServiceEnabled",
    "SystemdServiceState",
    "WaitForFactCommand",
    "is_active",
    "server",
    "service",
    "unit_name",
    "wait_service",
]
