# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""The ``hardware`` module namespace.

Re-exports everything from ``hardware.py`` so the facts
(``hardware.BlockDevices``, ``hardware.CpuInfo``, ``hardware.Cpus``,
``hardware.DiskStats``, ``hardware.DmiInfo``, ``hardware.Ipv4Addrs``,
``hardware.Ipv6Addrs``, ``hardware.LsBlk``, ``hardware.NetworkDevices``,
``hardware.SmbiosInfo``)
are reachable through the package name alone.
"""

from . import hardware
from .hardware import (
    BlockDevices,
    CpuInfo,
    Cpus,
    DiskStats,
    DmiInfo,
    Ipv4Addrs,
    Ipv6Addrs,
    LsBlk,
    NetworkDevices,
    SmbiosInfo,
)

__all__ = [
    "BlockDevices",
    "CpuInfo",
    "Cpus",
    "DiskStats",
    "DmiInfo",
    "Ipv4Addrs",
    "Ipv6Addrs",
    "LsBlk",
    "NetworkDevices",
    "SmbiosInfo",
    "hardware",
]