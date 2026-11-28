# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""The ``zabbix`` module namespace.

Re-exports everything from ``zabbix.py`` so the JSON-RPC session class
(``zabbix.ZabbixClient``, its ``zabbix.ZabbixAPIError`` and the
``zabbix.connect`` factory) and the three control-side coroutines the
Ansible API branch maps onto (``zabbix.host_group``/``zabbix.host``/
``zabbix.host_macro``) are reachable through the package name alone.

These are **not** ``op()`` operations -- they run on the controller against
the Zabbix server's HTTP endpoint, so a deploy awaits them directly or through
a ``control`` barrier rather than yielding command descriptors.
"""

from . import zabbix
from .zabbix import (
    ZabbixAPIError,
    ZabbixClient,
    connect,
    host,
    host_group,
    host_macro,
)

__all__ = [
    "ZabbixAPIError",
    "ZabbixClient",
    "connect",
    "host",
    "host_group",
    "host_macro",
    "zabbix",
]
