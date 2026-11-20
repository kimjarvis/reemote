# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""The ``hardware`` module namespace.

Re-exports everything from ``hardware.py`` so the facts
(``hardware.BlockDevices``, ``hardware.CpuInfo``, ``hardware.Cpus``,
``hardware.DefaultIpv4Address``, ``hardware.DiskStats``, ``hardware.DmiInfo``,
``hardware.Ipv4Addrs``,
``hardware.Ipv6Addrs``, ``hardware.LsBlk``, ``hardware.Memory``,
``hardware.NetworkDevices``, ``hardware.SmbiosInfo``,
``hardware.SwapEnabled``) and their entry TypedDicts
(``hardware.BlockDevice``, ``hardware.DefaultIpv4AddressInfo``,
``hardware.DiskStat``, ``hardware.LsBlkNode``,
``hardware.NetworkDevice``, ``hardware.Ipv4Address``,
``hardware.Ipv6Address``)
are reachable through the package name alone.
"""

from . import hardware
from .hardware import (
    BlockDevice,
    BlockDevices,
    CpuInfo,
    Cpus,
    DefaultIpv4Address,
    DefaultIpv4AddressInfo,
    DiskStat,
    DiskStats,
    DmiInfo,
    Ipv4Address,
    Ipv4Addrs,
    Ipv6Address,
    Ipv6Addrs,
    LsBlk,
    LsBlkNode,
    Memory,
    NetworkDevice,
    NetworkDevices,
    SmbiosInfo,
    SwapEnabled,
)

__all__ = [
    "BlockDevice",
    "BlockDevices",
    "CpuInfo",
    "Cpus",
    "DefaultIpv4Address",
    "DefaultIpv4AddressInfo",
    "DiskStat",
    "DiskStats",
    "DmiInfo",
    "Ipv4Address",
    "Ipv4Addrs",
    "Ipv6Address",
    "Ipv6Addrs",
    "LsBlk",
    "LsBlkNode",
    "Memory",
    "NetworkDevice",
    "NetworkDevices",
    "SmbiosInfo",
    "SwapEnabled",
    "hardware",
]