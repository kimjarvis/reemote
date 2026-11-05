# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The ``hardware`` module: ``BlockDevices``, ``CpuInfo``, ``Cpus``,
# ``DiskStats``, ``DmiInfo``, ``Ipv4Addrs``, ``Ipv6Addrs``, ``LsBlk``,
# ``NetworkDevices`` and ``SmbiosInfo``.
#
# Fact side: ``BlockDevices`` (the mounted block devices and their disk usage,
# parsed from ``df``), ``CpuInfo`` (the CPU details reported by ``lscpu``),
# ``Cpus`` (the number of online CPUs reported by ``getconf``),
# ``DiskStats`` (the per-device IO counters read from ``/proc/diskstats``),
# ``DmiInfo`` (the system identity, BIOS and chassis fields exposed under
# ``/sys/class/dmi/id/``), ``Ipv4Addrs`` (the IPv4 addresses per network
# interface), ``Ipv6Addrs`` (the IPv6 addresses per network interface),
# ``LsBlk`` (the complete block device tree reported by ``lsblk -J``,
# mounted or not), ``NetworkDevices`` (the full network device details --
# ether, mtu, state and both address families with their mask/broadcast
# details), the last three parsed from ``ip addr show`` or ``ifconfig -a``,
# and ``SmbiosInfo`` (the full SMBIOS/DMI tables decoded by ``dmidecode``).
# ``DiskStats``, ``DmiInfo``, ``LsBlk`` and ``SmbiosInfo`` are reemote5
# originals with no pyinfra counterpart: ``DiskStats`` and ``LsBlk``
# complement ``BlockDevices``, whose ``df`` listing only shows mounted
# filesystems, while ``DmiInfo`` and ``SmbiosInfo`` report the machine's
# firmware identity -- manufacturer, product name, serial number, BIOS
# version and chassis type -- for asset tracking and conditional execution.
# ``BlockDevices``, ``DiskStats``, ``LsBlk`` and ``NetworkDevices`` type
# their record dicts as the exported ``BlockDevice``, ``DiskStat``,
# ``LsBlkNode`` and ``NetworkDevice`` TypedDicts (the latter with
# ``Ipv4Address``/``Ipv6Address`` address blocks), keeping the outer
# device-keyed mappings unchanged. ``CpuInfo``, ``DmiInfo`` and
# ``SmbiosInfo`` keep plain dicts because their keys are dynamic tool
# labels, and the ``Ipv4Addrs``/``Ipv6Addrs`` projections keep their
# interface -> address-string lists -- the agreed exceptions to the
# "facts return TypedDicts" rule.
# Like every fact they are phase-unaware: the ``process`` coroutine just gathers
# the raw value. The namespace holds no operations yet.
# """

import json
import re
from collections.abc import Iterable
from typing import Any, NotRequired, TypedDict, cast

from ...hosts import Host

# pyinfra's row regex, verbatim: device, blocks, used, available, a 1-3 digit
# percentage and the mount point. Rows whose device or mount point carries a
# character outside these classes (e.g. an NFS ``host:/export`` source) do
# not match and are skipped, exactly like pyinfra.
_BLOCK_DEVICE_ROW_RE = re.compile(
    r"([a-zA-Z0-9\/\-_]+)\s+([0-9]+)\s+([0-9]+)\s+([0-9]+)\s+([0-9]{1,3})%\s+([a-zA-Z\/0-9\-_]+)"
)

# pyinfra's NetworkDevices regexes, verbatim, shared by NetworkDevices and
# the Ipv4Addrs/Ipv6Addrs projections: the per-device section splitter,
# the device-name matcher, and the two inet/inet6 row shapes per family
# (the `ip addr show` one is tried first; the `ifconfig -a` one requires
# the netmask/prefixlen token, exactly like pyinfra, so a token-less
# ifconfig row never matches).
_DEVICE_SECTION_RE = re.compile(r"\n(?=\d+: [^\s/:]|[^\s/:]+:.*mtu )")
_DEVICE_NAME_RE = re.compile(r"^(?:\d+: )?([^\s/:]+):")
_IPV4_ROW_RES = (
    # ip addr show
    re.compile(
        r"inet (?P<address>\d+\.\d+\.\d+\.\d+)/(?P<mask>\d+)(?: metric \d+)?(?: brd (?P<broadcast>\d+\.\d+\.\d+\.\d+))?"
    ),
    # ifconfig -a
    re.compile(
        r"inet (?P<address>\d+\.\d+\.\d+\.\d+)\s+netmask\s+(?P<mask>(?:\d+\.\d+\.\d+\.\d+)|(?:[0-9a-fA-FxX]+))(?:\s+broadcast\s+(?P<broadcast>\d+\.\d+\.\d+\.\d+))?"
    ),
)
_IPV6_ROW_RES = (
    # ip addr show
    re.compile(r"inet6\s+(?P<address>[0-9a-fA-F:]+)/(?P<mask>\d+)"),
    # ifconfig -a
    re.compile(r"inet6\s+(?P<address>[0-9a-fA-F:]+)\s+prefixlen\s+(?P<mask>\d+)"),
)
# pyinfra's NetworkDevices ether/mtu matchers, verbatim: a 17-character
# MAC after the ``ether`` token and the first ``mtu`` count in a section.
_ETHER_RE = re.compile(r"ether ([0-9A-Fa-f:]{17})")
_MTU_RE = re.compile(r"mtu (\d+)")

# The /proc/diskstats counter columns, in kernel order (Documentation/
# admin-guide/iostats.rst): the eleven classic counters every kernel since
# 2.6 prints, the four discard counters added in 4.18 and the two flush
# counters added in 5.5. A row carries 3 leading fields (major, minor,
# device name) plus whichever counter groups its kernel supports.
_DISKSTATS_FIELDS = (
    "reads_completed",
    "reads_merged",
    "sectors_read",
    "ms_reading",
    "writes_completed",
    "writes_merged",
    "sectors_written",
    "ms_writing",
    "ios_in_progress",
    "ms_doing_io",
    "weighted_ms_doing_io",
)
_DISKSTATS_DISCARD_FIELDS = (
    "discards_completed",
    "discards_merged",
    "sectors_discarded",
    "ms_discarding",
)
_DISKSTATS_FLUSH_FIELDS = ("flush_requests", "ms_flushing")

# The sysfs directory the kernel exposes the DMI identity fields under.
# ``DmiInfo`` keys its dict by the file names below this prefix and skips
# the directory's non-identity entries (``uevent``, ``modalias``).
_DMI_ID_PREFIX = "/sys/class/dmi/id/"


def _addrs_from_json(lines: list[str], family: str) -> dict[str, list[str]]:
    """Project ``ip -j addr show`` JSON to interface -> address list.

    ``family`` is the ``ip -j`` address family to keep (``inet`` for
    ``Ipv4Addrs``, ``inet6`` for ``Ipv6Addrs``). Malformed JSON reads as {}
    (pyinfra logs an error and returns its empty device map); devices
    without an ``ifname`` and addr entries without a ``local``/``family``
    are skipped, and only ``family`` entries are kept.
    """
    try:
        decoded = json.loads("\n".join(lines))
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError, RecursionError):
        return {}
    # Non-dict entries would crash pyinfra's walker; reemote5 hardens the
    # parser by skipping them instead.
    if not isinstance(decoded, list):
        return {}
    addrs: dict[str, list[str]] = {}
    for info in decoded:
        if not isinstance(info, dict) or "ifname" not in info:
            continue
        ips = [
            addr_info["local"]
            for addr_info in info.get("addr_info") or []
            if isinstance(addr_info, dict)
            and addr_info.get("family") == family
            and "local" in addr_info
        ]
        # pyinfra's projection drops an interface whose primary address is
        # empty, but keeps any aliases, in listing order.
        if ips and ips[0]:
            addrs[info["ifname"]] = ips
    return addrs


def _addrs_from_text(
    lines: list[str], row_res: tuple[re.Pattern[str], ...]
) -> dict[str, list[str]]:
    """Project ``ip addr show``/``ifconfig -a`` text to the address lists.

    ``row_res`` are the family's two row shapes, the `ip addr show` one
    first, exactly like pyinfra's per-family regex tuples.
    """
    text = "\n".join(line.strip() for line in lines)
    addrs: dict[str, list[str]] = {}
    for section in _DEVICE_SECTION_RE.split(text):
        name_match = _DEVICE_NAME_RE.match(section)
        if not name_match:
            continue
        name = name_match.group(1)
        ips: list[str] = []
        for row_re in row_res:
            matches = list(row_re.finditer(section))
            if matches:
                ips = [match.group("address") for match in matches]
                break
        # A later section for the same name wins, like pyinfra's device
        # map -- including dropping the interface when the last section
        # carries no address of the projected family.
        if ips:
            addrs[name] = ips
        else:
            addrs.pop(name, None)
    return addrs


def _mask(value: str) -> tuple[int, str]:
    """Convert a mask token to (mask_bits, netmask). pyinfra's ``mask``, verbatim.

    Accepts a prefix length (``24``), a hex netmask (``0xffffff00``) or a
    dotted-quad netmask (``255.255.255.0``); the bit count of a hex/dotted
    mask is the population count, and a prefix length is expanded to its
    dotted-quad netmask.
    """
    try:
        mask_bits = int(value, 16).bit_count() if value.startswith("0x") else int(value)
        netmask = ".".join(
            str((0xFFFFFFFF << (32 - b) >> mask_bits) & 0xFF) for b in (24, 16, 8, 0)
        )
    except ValueError:
        mask_bits = sum(int(x).bit_count() for x in value.split("."))
        netmask = value
    return mask_bits, netmask


def _devices_from_json(lines: list[str]) -> "dict[str, NetworkDevice]":
    """Parse ``ip -j addr show`` JSON into the device -> details dict.

    Malformed JSON reads as {} (pyinfra logs an error and returns its
    empty device map); devices without an ``ifname`` and addr entries
    without a ``local``/``family`` are skipped (pyinfra logs and keeps
    going). The first addr entry of each family becomes the primary
    ``ipv4``/``ipv6`` block, later ones collect under ``additional_ips``.
    """
    try:
        decoded = json.loads("\n".join(lines))
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError, RecursionError):
        return {}
    # Non-dict entries would crash pyinfra's walker; reemote5 hardens the
    # parser by skipping them instead.
    if not isinstance(decoded, list):
        return {}
    devices: dict[str, NetworkDevice] = {}
    for info in decoded:
        if not isinstance(info, dict) or "ifname" not in info:
            continue
        device: dict[str, Any] = {"state": info.get("operstate") or "UNKNOWN"}
        if "address" in info:
            device["ether"] = info["address"]
        if "mtu" in info:
            device["mtu"] = info["mtu"]
        for addr_info in info.get("addr_info") or []:
            if not isinstance(addr_info, dict) or "local" not in addr_info:
                continue
            family = {"inet": "ipv4", "inet6": "ipv6"}.get(addr_info.get("family"))
            if family is None:
                continue
            addr_blk: dict[str, Any] = {"address": addr_info["local"]}
            if family == "ipv4":
                addr_blk["broadcast"] = addr_info.get("broadcast")
            if "prefixlen" in addr_info:
                addr_blk["mask_bits"] = addr_info["prefixlen"]
                if family == "ipv4":
                    addr_blk["netmask"] = _mask(str(addr_info["prefixlen"]))[1]
            if family not in device:
                device[family] = addr_blk
            else:
                device[family].setdefault("additional_ips", []).append(addr_blk)
        # The family blocks are filled dynamically above, so the device
        # dict is cast to the TypedDict shape the parser documents.
        devices[info["ifname"]] = cast("NetworkDevice", device)
    return devices


def _devices_from_text(lines: list[str]) -> "dict[str, NetworkDevice]":
    """Parse ``ip addr show``/``ifconfig -a`` text into the device dict.

    Each device section contributes its state heuristic (``UP``/``DOWN``
    token anywhere in the section, else ``UNKNOWN``), the optional
    ether/mtu, and the first matching row shape per family -- the primary
    address plus any ``additional_ips`` aliases. A later section for the
    same name wins outright, like pyinfra's device map.
    """
    text = "\n".join(line.strip() for line in lines)
    devices: dict[str, NetworkDevice] = {}
    for section in _DEVICE_SECTION_RE.split(text):
        name_match = _DEVICE_NAME_RE.match(section)
        if not name_match:
            continue
        name = name_match.group(1)
        device: dict[str, Any] = {
            "state": "UP" if "UP" in section else "DOWN" if "DOWN" in section else "UNKNOWN"
        }
        ether = _ETHER_RE.search(section)
        if ether:
            device["ether"] = ether.group(1)
        mtu = _MTU_RE.search(section)
        if mtu:
            device["mtu"] = int(mtu.group(1))
        for family, row_res in (("ipv4", _IPV4_ROW_RES), ("ipv6", _IPV6_ROW_RES)):
            matches: list[re.Match[str]] = []
            for row_re in row_res:
                matches = list(row_re.finditer(section))
                if matches:
                    break
            if not matches:
                continue
            addrs: list[dict[str, Any]] = []
            for match in matches:
                if family == "ipv4":
                    mask_bits, netmask = _mask(match.group("mask"))
                    addrs.append(
                        {
                            "address": match.group("address"),
                            "mask_bits": mask_bits,
                            "netmask": netmask,
                            "broadcast": match.group("broadcast"),
                        }
                    )
                else:
                    addrs.append(
                        {
                            "address": match.group("address"),
                            "mask_bits": int(match.group("mask")),
                        }
                    )
            device[family] = addrs[0]
            if len(addrs) > 1:
                device[family]["additional_ips"] = addrs[1:]
        # The family blocks are filled dynamically above, so the device
        # dict is cast to the TypedDict shape the parser documents.
        devices[name] = cast("NetworkDevice", device)
    return devices


def _copy_lsblk_nodes(entries: Any) -> "list[LsBlkNode]":
    """Copy ``lsblk -J`` tree nodes, keeping only well-formed dict entries.

    Each node dict is copied and its ``children`` list rebuilt recursively,
    so a seam reader can never mutate the simulated state. Non-dict
    entries are skipped at every level and a non-list ``children`` value
    is dropped; a non-list input reads as [] (the shared empty/undecodable
    fallback of ``LsBlk``).
    """
    if not isinstance(entries, list):
        return []
    nodes: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        node = dict(entry)
        if isinstance(node.get("children"), list):
            node["children"] = _copy_lsblk_nodes(node["children"])
        elif "children" in node:
            del node["children"]
        nodes.append(node)
    # The nodes carry whatever keys the JSON document held (the parser
    # hardens rather than validates), so the copy is cast to the TypedDict
    # shape the fact's ``-o`` column list documents.
    return cast("list[LsBlkNode]", nodes)


def _copy_device(info: "NetworkDevice") -> "NetworkDevice":
    """Copy a device details dict, including the nested address blocks.

    The ipv4/ipv6 blocks and any ``additional_ips`` entry dicts are copied
    too, so a seam reader can never mutate the simulated state.
    """
    copied = dict(info)
    for family in ("ipv4", "ipv6"):
        if family in copied:
            addr = dict(copied[family])
            if "additional_ips" in addr:
                addr["additional_ips"] = [dict(ip) for ip in addr["additional_ips"]]
            copied[family] = addr
    return cast("NetworkDevice", copied)


# --- Facts -------------------------------------------------------------------


class BlockDevice(TypedDict):
    """
    Attributes:
        blocks: The filesystem's total 1K-block count, as the raw string
            ``df`` prints.
        used: The used block count, as a raw string.
        available: The available block count, as a raw string.
        used_percent: The use percentage without the ``%`` sign, as a
            raw string.
        mount: The mount point path.
    """

    # One mounted block device (``BlockDevices``' entry), as a plain dict.
    #
    # A TypedDict rather than a class instance, like ``AptKey``: entries
    # are ordinary dicts, so equality against a literal dict works and
    # ``json.dumps`` serializes them directly. Every value stays a raw
    # string -- pyinfra does not convert the block counts or the
    # percentage to ints.

    blocks: str
    used: str
    available: str
    used_percent: str
    mount: str


class BlockDevices:
    """The mounted block devices, as a dict of device -> ``BlockDevice`` dict.

    **Commands:**

    ```bash
    df
    ```

    **Returns:**

    ```python
    dict[str, BlockDevice]
    ```

    Examples:
        Examples can be found in ``examples/modules/hardware/hardware_block_devices.py``.
    """

    # Mirrors pyinfra's ``hardware.BlockDevices``: ``df`` lists the mounted
    # filesystems and each matching row maps the device to its
    # ``BlockDevice`` entry -- {blocks, used, available, used_percent,
    # mount} -- for example::
    #
    #     {
    #         "/dev/sda1": {
    #             "available": "39489508",
    #             "used_percent": "3",
    #             "mount": "/",
    #             "used": "836392",
    #             "blocks": "40325900",
    #         },
    #     }
    #
    # Every value stays a raw string (pyinfra does not convert the block
    # counts or the percentage to ints). The header row and any line that
    # does not match the ``df`` row shape are skipped, as is the ``none``
    # pseudo-filesystem; a repeated device keeps the last row. Empty output
    # (a failed read) maps to ``{}``, pyinfra's ``default = dict``.
    #
    # Because ``df`` only lists mounted filesystems, unmounted disks, LVM
    # volumes and raw devices never appear here; the reemote5-original
    # ``LsBlk`` sibling reports the complete block device tree and
    # ``DiskStats`` the per-device IO counters.

    #: Shell used to gather the mounted block devices over SSH.
    COMMAND = "df"

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, BlockDevice]:
        """Parse ``df`` rows into the device -> usage dict. Pure and testable.

        Non-matching lines (the header, blank lines, devices or mount
        points outside the regex's character classes) and ``none`` rows
        are skipped; all five values are kept as raw strings.
        """
        devices: dict[str, BlockDevice] = {}
        for line in output:
            matches = _BLOCK_DEVICE_ROW_RE.match(line)
            if matches:
                if matches.group(1) == "none":
                    continue
                devices[matches.group(1)] = {
                    "blocks": matches.group(2),
                    "used": matches.group(3),
                    "available": matches.group(4),
                    "used_percent": matches.group(5),
                    "mount": matches.group(6),
                }
        return devices

    @classmethod
    async def process(cls, host: Host) -> dict[str, BlockDevice]:
        state = getattr(host, "block_devices", None)
        if state is not None:
            # SimulatedHost seam: read the in-memory state directly. Copy
            # the nested dicts so callers never mutate the simulated state
            # (a TypedDict entry IS a plain dict at runtime).
            return {
                device: cast("BlockDevice", dict(info))
                for device, info in state.items()
            }
        # Real transport: `df` over asyncssh from the host's connection.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class CpuInfo:
    """The CPU details reported by ``lscpu``, as a dict (or None).

    **Commands:**

    ```bash
    LANG=C lscpu
    ```

    **Returns:**

    ```python
    dict[str, str | list[str]] | None
    ```

    Examples:
        Examples can be found in ``examples/modules/hardware/hardware_cpu_info.py``.
    """

    # Mirrors pyinfra's ``hardware.CpuInfo``: ``LANG=C lscpu`` prints
    # ``Key: value`` rows, and each row maps its stripped key to its stripped
    # value, for example::
    #
    #     {
    #         "Architecture": "x86_64",
    #         "CPU(s)": "4",
    #         "Vendor ID": "GenuineIntel",
    #         "Model name": "Intel(R) Atom(TM) CPU N2800   @ 1.86GHz",
    #         "Flags": ["fpu", "vme", "de", "pse", "tsc", "msr"],
    #     }
    #
    # A key containing ``flag`` (case-insensitively, e.g. ``Flags``) has its
    # value split on whitespace into a list of tokens; every other value stays
    # a raw string. As in pyinfra the value is the segment after the *first*
    # colon (``line.split(":")[1]``), so a second colon in a value truncates it
    # -- no ``lscpu`` row in pyinfra's fixtures carries one. Lines without a
    # colon are skipped (pyinfra would raise ``IndexError`` on them; reemote5
    # hardens the parser). Empty output (a failed read) reads as ``None``,
    # exactly like pyinfra's ``invalid`` fixture. pyinfra's ``requires_command``
    # gating is deliberately dropped: a *missing* ``lscpu`` exits 127, which
    # ``run_shell`` reports as ``CommandNotFoundError`` rather than ``None``.
    #
    # The dict stays plain (no entry TypedDict): its keys are ``lscpu``'s
    # open-ended labels, which vary with the tool version, so a fixed field
    # list would go stale -- the agreed dynamic-key exception to the
    # "facts return TypedDicts" rule (``DmiInfo`` and ``SmbiosInfo`` are the
    # same case).

    #: Shell used to gather the CPU details over SSH.
    COMMAND = "LANG=C lscpu"

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, str | list[str]] | None:
        """Parse ``lscpu`` rows into the CPU info dict, or None. Pure/testable.

        Each colon-bearing row maps its stripped key to its stripped value
        (the segment after the first colon, pyinfra's ``split(":")[1]``); a
        key containing ``flag`` has its value split into a list of tokens.
        Colon-less lines are skipped; an empty result reads as ``None``.
        """
        cpu_info: dict[str, str | list[str]] = {}
        for line in output:
            info_data = line.split(":")
            if len(info_data) < 2:
                continue
            key = info_data[0].strip()
            if "flag" in key.lower():
                cpu_info[key] = info_data[1].strip().split()
            else:
                cpu_info[key] = info_data[1].strip()
        return cpu_info or None

    @classmethod
    async def process(cls, host: Host) -> dict[str, str | list[str]] | None:
        state = getattr(host, "cpu_info", None)
        if state is not None:
            # SimulatedHost seam: read the in-memory state directly. Copy the
            # dict and any list values so callers never mutate the simulated
            # state (str values are immutable).
            return {
                key: list(value) if isinstance(value, list) else value
                for key, value in state.items()
            }
        # Real transport: `LANG=C lscpu` over asyncssh from the connection.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class Cpus:
    """The number of online CPUs on the machine, as an int (or None).

    **Commands:**

    ```bash
    getconf NPROCESSORS_ONLN 2> /dev/null || getconf _NPROCESSORS_ONLN
    ```

    **Returns:**

    ```python
    int | None
    ```

    Examples:
        Examples can be found in ``examples/modules/hardware/hardware_cpus.py``.
    """

    # Mirrors pyinfra's ``hardware.Cpus``: ``getconf NPROCESSORS_ONLN`` (with
    # a fallback to the underscore-prefixed ``_NPROCESSORS_ONLN`` name for
    # platforms without the plain one) prints the count of online processors,
    # and the first output line is converted with ``int()``, for example::
    #
    #     1
    #
    # Anything after the first line is ignored (pyinfra's ``list(output)[0]``).
    # A first line that is not an integer reads as ``None`` -- pyinfra's
    # ``invalid`` fixture ("nope") -- as does empty output (a failed read),
    # which pyinfra answers with its ``default()`` fallback of ``None``.

    #: Shell used to gather the online CPU count over SSH.
    COMMAND = "getconf NPROCESSORS_ONLN 2> /dev/null || getconf _NPROCESSORS_ONLN"

    @classmethod
    def parse(cls, output: Iterable[str]) -> int | None:
        """Parse ``getconf`` output into the CPU count, or None. Pure/testable.

        Only the first line is converted, with ``int()`` (which tolerates
        surrounding whitespace); a non-integer first line or no output at
        all reads as ``None``.
        """
        for line in output:
            try:
                return int(line)
            except ValueError:
                return None
        return None

    @classmethod
    async def process(cls, host: Host) -> int | None:
        cpus = getattr(host, "cpus", None)
        if cpus is not None:
            # SimulatedHost seam: read the in-memory count directly.
            # int is immutable, so no copy is needed.
            return cpus
        # Real transport: `getconf` over asyncssh from the host's connection.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class DiskStat(TypedDict):
    """
    Attributes:
        reads_completed: Reads completed successfully.
        reads_merged: Reads merged before queueing.
        sectors_read: Sectors read (512-byte units).
        ms_reading: Milliseconds spent reading.
        writes_completed: Writes completed successfully.
        writes_merged: Writes merged before queueing.
        sectors_written: Sectors written (512-byte units).
        ms_writing: Milliseconds spent writing.
        ios_in_progress: IOs currently in flight.
        ms_doing_io: Milliseconds spent doing IOs.
        weighted_ms_doing_io: Milliseconds spent doing IOs, weighted by
            the number of IOs in flight.
        discards_completed: Discards completed successfully, on a 4.18+
            kernel that prints the discard group.
        discards_merged: Discards merged before queueing, on a 4.18+
            kernel that prints the discard group.
        sectors_discarded: Sectors discarded (512-byte units), on a
            4.18+ kernel that prints the discard group.
        ms_discarding: Milliseconds spent discarding, on a 4.18+ kernel
            that prints the discard group.
        flush_requests: Flush requests completed, on a 5.5+ kernel that
            prints the flush group.
        ms_flushing: Milliseconds spent flushing, on a 5.5+ kernel that
            prints the flush group.
    """

    # One device's IO counters (``DiskStats``' entry), as a plain dict.
    #
    # A TypedDict rather than a class instance, like ``BlockDevice``:
    # entries are ordinary dicts, so equality against a literal dict
    # works and ``json.dumps`` serializes them directly. The eleven
    # classic counters are always present; the discard group (kernel
    # 4.18+) and the flush group (kernel 5.5+) are NotRequired because
    # the parser includes each group only when the /proc/diskstats row
    # carries it. All counters are cumulative since boot.

    reads_completed: int
    reads_merged: int
    sectors_read: int
    ms_reading: int
    writes_completed: int
    writes_merged: int
    sectors_written: int
    ms_writing: int
    ios_in_progress: int
    ms_doing_io: int
    weighted_ms_doing_io: int
    discards_completed: NotRequired[int]
    discards_merged: NotRequired[int]
    sectors_discarded: NotRequired[int]
    ms_discarding: NotRequired[int]
    flush_requests: NotRequired[int]
    ms_flushing: NotRequired[int]


class DiskStats:
    """The per-device IO counters, as a dict of device -> ``DiskStat`` dict.

    **Commands:**

    ```bash
    cat /proc/diskstats
    ```

    **Returns:**

    ```python
    dict[str, DiskStat]
    ```

    Examples:
        Examples can be found in ``examples/modules/hardware/hardware_disk_stats.py``.
    """

    # A reemote5 original (pyinfra has no counterpart): ``cat
    # /proc/diskstats`` lists one row per block device -- mounted or not --
    # and each maps its device name to the kernel's IO counters as a
    # ``DiskStat`` entry of ints, for example::
    #
    #     {
    #         "sda": {
    #             "reads_completed": 12345,
    #             "reads_merged": 678,
    #             "sectors_read": 901234,
    #             "ms_reading": 5678,
    #             "writes_completed": 23456,
    #             "writes_merged": 7890,
    #             "sectors_written": 1234567,
    #             "ms_writing": 8901,
    #             "ios_in_progress": 0,
    #             "ms_doing_io": 12345,
    #             "weighted_ms_doing_io": 14567,
    #         },
    #     }
    #
    # ``/proc/diskstats`` is read directly rather than through ``iostat``
    # so no sysstat package is required on the target. The eleven classic
    # counters are always present; a 4.18+ kernel adds the four
    # ``discards_*``/``ms_discarding`` counters and a 5.5+ kernel the two
    # ``flush_requests``/``ms_flushing`` counters, each group included only
    # when the row carries it. Sectors are 512-byte units regardless of the
    # device's physical sector size. The counters are cumulative since
    # boot: a single gather is a snapshot, and a rate needs two gathers
    # subtracted over their elapsed time. Every row is kept -- including
    # ``loop``/``ram``/``dm-*`` devices -- so the caller can filter; a
    # repeated device keeps the last row. Rows with fewer than the eleven
    # classic counters or a non-integer counter are skipped, and any
    # columns beyond the flush group (a future kernel) are ignored. Empty
    # output (a failed read) maps to ``{}``, the house dict-fact fallback.

    #: Shell used to gather the per-device IO counters over SSH.
    COMMAND = "cat /proc/diskstats"

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, DiskStat]:
        """Parse ``/proc/diskstats`` rows into the counter dict. Pure/testable.

        Each row's third field keys the device; the counter columns are
        named in kernel order, with the discard and flush groups appended
        only when the row is long enough to carry them. Short rows and
        rows whose counters are not all integers are skipped.
        """
        stats: dict[str, DiskStat] = {}
        for line in output:
            fields = line.split()
            if len(fields) < 3 + len(_DISKSTATS_FIELDS):
                continue
            names = _DISKSTATS_FIELDS
            if len(fields) >= 3 + len(names) + len(_DISKSTATS_DISCARD_FIELDS):
                names += _DISKSTATS_DISCARD_FIELDS
            if len(fields) >= 3 + len(names) + len(_DISKSTATS_FLUSH_FIELDS):
                names += _DISKSTATS_FLUSH_FIELDS
            try:
                values = [int(value) for value in fields[3 : 3 + len(names)]]
            except ValueError:
                continue
            # The counter names are assembled per kernel group, so the
            # zip-built dict is cast to the TypedDict shape it matches.
            stats[fields[2]] = cast("DiskStat", dict(zip(names, values, strict=True)))
        return stats

    @classmethod
    async def process(cls, host: Host) -> dict[str, DiskStat]:
        state = getattr(host, "disk_stats", None)
        if state is not None:
            # SimulatedHost seam: read the in-memory state directly. Copy
            # the nested dicts so callers never mutate the simulated state
            # (a TypedDict entry IS a plain dict at runtime).
            return {
                device: cast("DiskStat", dict(counters))
                for device, counters in state.items()
            }
        # Real transport: `cat /proc/diskstats` over asyncssh from the
        # host's connection.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class DmiInfo:
    """The DMI identity fields from sysfs, as a dict of field -> value.

    **Commands:**

    ```bash
    grep -H '' /sys/class/dmi/id/* 2> /dev/null || true
    ```

    **Returns:**

    ```python
    dict[str, str]
    ```

    Examples:
        Examples can be found in ``examples/modules/hardware/hardware_dmi_info.py``.
    """

    # A reemote5 original (pyinfra has no counterpart): ``grep -H ''
    # /sys/class/dmi/id/*`` dumps the kernel-exposed DMI identity files as
    # ``path:value`` rows, and each maps its file name to its stripped
    # value, for example::
    #
    #     {
    #         "sys_vendor": "Dell Inc.",
    #         "product_name": "PowerEdge R640",
    #         "product_serial": "7XYZ123",
    #         "bios_vendor": "Dell Inc.",
    #         "bios_version": "2.12.2",
    #         "bios_date": "07/09/2021",
    #         "chassis_type": "23",
    #     }
    #
    # Reading sysfs needs no ``dmidecode`` binary and no root for most
    # fields, so this is the unprivileged sibling of ``SmbiosInfo`` (which
    # decodes the full SMBIOS tables but usually requires root). The
    # root-only files (``product_serial``, ``product_uuid``,
    # ``board_serial``, ``chassis_serial``) are simply absent when the
    # login user cannot read them -- their read errors go to ``/dev/null``.
    # Values stay raw strings: ``chassis_type`` is the numeric SMBIOS
    # chassis code (e.g. ``3`` desktop, ``10`` notebook, ``23`` rack mount;
    # ``SmbiosInfo`` reports the decoded label instead). The directory's
    # ``uevent`` and ``modalias`` entries are skipped, as are empty values
    # and any line outside the ``/sys/class/dmi/id/`` prefix. A machine
    # without DMI (most containers, many ARM boards and VMs) has no such
    # directory: the failed read maps to ``{}``, the house dict-fact
    # fallback, so an empty dict means "no DMI identity available".
    #
    # The dict stays plain (no entry TypedDict): its keys are the sysfs
    # file basenames the kernel exposes, an open-ended set that varies
    # with the kernel version and the login user's read access, so a
    # fixed field list would go stale -- the agreed dynamic-key exception
    # to the "facts return TypedDicts" rule (``CpuInfo`` and ``SmbiosInfo``
    # are the same case).

    #: Shell used to gather the DMI identity fields over SSH: grep prefixes
    #: each value with its file path; unreadable files and a missing
    #: directory are swallowed so the fact degrades to {}.
    COMMAND = "grep -H '' /sys/class/dmi/id/* 2> /dev/null || true"

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, str]:
        """Parse ``grep -H`` sysfs rows into the field dict. Pure and testable.

        Each ``/sys/class/dmi/id/<field>:<value>`` row maps its file name
        to the stripped value (the segment after the first colon, so a
        colon inside a value survives). Rows outside the prefix, the
        ``uevent``/``modalias`` entries and empty values are skipped.
        """
        info: dict[str, str] = {}
        for line in output:
            path, sep, value = line.partition(":")
            if not sep or not path.startswith(_DMI_ID_PREFIX):
                continue
            key = path.rsplit("/", 1)[-1]
            if key in ("uevent", "modalias"):
                continue
            value = value.strip()
            if value:
                info[key] = value
        return info

    @classmethod
    async def process(cls, host: Host) -> dict[str, str]:
        state = getattr(host, "dmi_info", None)
        if state is not None:
            # SimulatedHost seam: read the in-memory state directly. Copy
            # the dict so callers never mutate the simulated state.
            return dict(state)
        # Real transport: the sysfs dump over asyncssh from the connection.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class Ipv4Addrs:
    """The IPv4 addresses per network interface, as interface -> list.

    **Commands:**

    ```bash
    ip -j addr show 2> /dev/null || ip addr show 2> /dev/null || ifconfig -a
    ```

    **Returns:**

    ```python
    dict[str, list[str]]
    ```

    Examples:
        Examples can be found in ``examples/modules/hardware/hardware_ipv4_addrs.py``.
    """

    # Mirrors pyinfra's ``hardware.Ipv4Addrs``: ``ip -j addr show`` (JSON)
    # or, failing that, ``ip addr show`` / ``ifconfig -a`` (text) lists the
    # interfaces, and each interface carrying IPv4 addresses maps to the
    # list of those addresses -- the primary address first, then any
    # aliases in listing order, for example::
    #
    #     {
    #         "eth0": ["127.0.0.1"],
    #     }
    #
    # Interfaces without an IPv4 address are not part of the dict. The
    # addresses are the raw strings the command printed (no octet
    # validation, as pyinfra's own fixture shows). Empty or undecodable
    # output reads as ``{}``, pyinfra's ``default = dict``.
    #
    # pyinfra implements this as a ``ShortFactBase`` projection over
    # ``hardware.NetworkDevices``; reemote5 collapses that indirection into
    # this single class (like ``HasGui``), keeping only the IPv4 address
    # lists (the IPv6 addresses are the sibling ``Ipv6Addrs`` fact's
    # projection); the richer device dict -- ether, mtu, state and each
    # address's mask/broadcast details -- is the sibling ``NetworkDevices``
    # fact. Entries the JSON walker cannot read (non-dict devices or address
    # entries) are skipped rather than raising; reemote5 hardens the parser.
    #
    # The projection keeps its plain dict[str, list[str]] shape (no entry
    # TypedDict): the values are lists of scalar address strings, not
    # record dicts -- the agreed exception to the "facts return
    # TypedDicts" rule. The typed address blocks live on the
    # ``NetworkDevices`` sibling instead.

    #: Shell used to gather the interface addresses over SSH: JSON first,
    #: then the plain-text `ip` and `ifconfig` fallbacks (pyinfra verbatim).
    COMMAND = "ip -j addr show 2> /dev/null || ip addr show 2> /dev/null || ifconfig -a"

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, list[str]]:
        """Parse ``ip``/``ifconfig`` output into the IPv4 lists. Pure/testable.

        pyinfra's JSON sniff decides the branch: when the first two
        stripped lines start with ``[{`` the output is ``ip -j`` JSON,
        otherwise it is parsed as device sections of text.
        """
        lines = list(output)
        if "".join(line.strip() for line in lines[:2]).startswith("[{"):
            return _addrs_from_json(lines, "inet")
        return _addrs_from_text(lines, _IPV4_ROW_RES)

    @classmethod
    async def process(cls, host: Host) -> dict[str, list[str]]:
        state = getattr(host, "ipv4_addrs", None)
        if state is not None:
            # SimulatedHost seam: read the in-memory state directly. Copy
            # the per-interface lists so callers never mutate the simulated
            # state.
            return {interface: list(ips) for interface, ips in state.items()}
        # Real transport: `ip`/`ifconfig` over asyncssh from the connection.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class Ipv6Addrs:
    """The IPv6 addresses per network interface, as interface -> list.

    **Commands:**

    ```bash
    ip -j addr show 2> /dev/null || ip addr show 2> /dev/null || ifconfig -a
    ```

    **Returns:**

    ```python
    dict[str, list[str]]
    ```

    Examples:
        Examples can be found in ``examples/modules/hardware/hardware_ipv6_addrs.py``.
    """

    # Mirrors pyinfra's ``hardware.Ipv6Addrs``: ``ip -j addr show`` (JSON)
    # or, failing that, ``ip addr show`` / ``ifconfig -a`` (text) lists the
    # interfaces, and each interface carrying IPv6 addresses maps to the
    # list of those addresses -- the primary address first, then any
    # aliases in listing order, for example::
    #
    #     {
    #         "eth0": ["fe80::a00:27ff::2"],
    #     }
    #
    # Interfaces without an IPv6 address are not part of the dict. The
    # addresses are the raw strings the command printed (no address
    # validation -- pyinfra's documented example above even carries a
    # doubled ``::``). Empty or undecodable output reads as ``{}``,
    # pyinfra's ``default = dict``.
    #
    # pyinfra implements this as a ``ShortFactBase`` projection over
    # ``hardware.NetworkDevices`` (a subclass of its ``Ipv4Addrs`` swapping
    # the projected family); reemote5 collapses that indirection into this
    # single sibling class sharing ``Ipv4Addrs``' command and projection
    # helpers, keeping only the IPv6 address lists; the richer device dict
    # -- ether, mtu, state and each address's mask details -- is the sibling
    # ``NetworkDevices`` fact. Entries the JSON walker cannot read (non-dict
    # devices or address entries) are skipped rather than raising; reemote5
    # hardens the parser.
    #
    # The projection keeps its plain dict[str, list[str]] shape (no entry
    # TypedDict): the values are lists of scalar address strings, not
    # record dicts -- the agreed exception to the "facts return
    # TypedDicts" rule, the same as ``Ipv4Addrs``. The typed address
    # blocks live on the ``NetworkDevices`` sibling instead.

    #: Shell used to gather the interface addresses over SSH: JSON first,
    #: then the plain-text `ip` and `ifconfig` fallbacks (pyinfra verbatim,
    #: the same command `Ipv4Addrs` runs).
    COMMAND = "ip -j addr show 2> /dev/null || ip addr show 2> /dev/null || ifconfig -a"

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, list[str]]:
        """Parse ``ip``/``ifconfig`` output into the IPv6 lists. Pure/testable.

        pyinfra's JSON sniff decides the branch: when the first two
        stripped lines start with ``[{`` the output is ``ip -j`` JSON,
        otherwise it is parsed as device sections of text.
        """
        lines = list(output)
        if "".join(line.strip() for line in lines[:2]).startswith("[{"):
            return _addrs_from_json(lines, "inet6")
        return _addrs_from_text(lines, _IPV6_ROW_RES)

    @classmethod
    async def process(cls, host: Host) -> dict[str, list[str]]:
        state = getattr(host, "ipv6_addrs", None)
        if state is not None:
            # SimulatedHost seam: read the in-memory state directly. Copy
            # the per-interface lists so callers never mutate the simulated
            # state.
            return {interface: list(ips) for interface, ips in state.items()}
        # Real transport: `ip`/`ifconfig` over asyncssh from the connection.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class LsBlkNode(TypedDict):
    """
    Attributes:
        name: The device name (e.g. ``sda``, ``sda1``).
        type: The device type (``disk``/``part``/``lvm``/...).
        size: The device size in bytes (``lsblk -b``).
        mountpoint: The mount point path, or None when unmounted (the
            singular ``MOUNTPOINT`` column reports only the last mount
            when a device is mounted at several paths).
        children: The stacked devices nested under this node, as further
            ``LsBlkNode`` entries; absent on a leaf device.
    """

    # One node of the ``lsblk -J`` tree (``LsBlk``'s entry), as a plain
    # dict.
    #
    # A TypedDict rather than a class instance, like ``BlockDevice``:
    # entries are ordinary dicts, so equality against a literal dict
    # works and ``json.dumps`` serializes them directly. The four fields
    # are the columns the fact's ``-o NAME,TYPE,SIZE,MOUNTPOINT`` list
    # requests; ``children`` is NotRequired because a leaf device carries
    # none, and it recurses so the tree structure stays typed at every
    # level.

    name: str
    type: str
    size: int
    mountpoint: str | None
    children: NotRequired[list["LsBlkNode"]]


class LsBlk:
    """The complete block device tree, as a list of ``LsBlkNode`` dicts.

    **Commands:**

    ```bash
    lsblk -J -b -o NAME,TYPE,SIZE,MOUNTPOINT
    ```

    **Returns:**

    ```python
    list[LsBlkNode]
    ```

    Examples:
        Examples can be found in ``examples/modules/hardware/hardware_lsblk.py``.
    """

    # A reemote5 original (pyinfra has no counterpart): ``lsblk -J -b -o
    # NAME,TYPE,SIZE,MOUNTPOINT`` prints the whole block device tree as
    # JSON -- including unmounted disks, partitions, LVM volumes and raw
    # devices that the ``df``-based ``BlockDevices`` sibling never sees --
    # and the fact returns its ``blockdevices`` array as ``LsBlkNode``
    # entries, for example::
    #
    #     [
    #         {
    #             "name": "sda",
    #             "type": "disk",
    #             "size": 21474836480,
    #             "mountpoint": None,
    #             "children": [
    #                 {
    #                     "name": "sda1",
    #                     "type": "part",
    #                     "size": 1073741824,
    #                     "mountpoint": "/boot",
    #                 },
    #             ],
    #         },
    #     ]
    #
    # Sizes are byte counts as ints (``-b``); ``mountpoint`` is None for an
    # unmounted device, and the singular ``MOUNTPOINT`` column reports only
    # the last mount when a device is mounted at several paths. Partitions,
    # LVM logical volumes and other stacked devices nest under their
    # parent's ``children`` list, so the tree structure is preserved
    # verbatim. Empty or undecodable output, a non-dict JSON document and a
    # missing/non-list ``blockdevices`` key all read as ``[]``, the house
    # empty-fallback for list facts. Non-dict nodes are skipped at every
    # level and a non-list ``children`` value is dropped rather than
    # raising; reemote5 hardens the parser. A *missing* ``lsblk`` exits
    # 127, which ``run_shell`` reports as ``CommandNotFoundError``.

    #: Shell used to gather the block device tree over SSH: JSON output,
    #: byte sizes and the four name/type/size/mountpoint columns.
    COMMAND = "lsblk -J -b -o NAME,TYPE,SIZE,MOUNTPOINT"

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[LsBlkNode]:
        """Parse ``lsblk -J`` output into the tree node list. Pure/testable.

        The JSON document's ``blockdevices`` array is returned with every
        node copied and its ``children`` rebuilt recursively; malformed
        JSON or an unusable document reads as ``[]``.
        """
        try:
            decoded = json.loads("\n".join(output))
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError, RecursionError):
            return []
        if not isinstance(decoded, dict):
            return []
        return _copy_lsblk_nodes(decoded.get("blockdevices"))

    @classmethod
    async def process(cls, host: Host) -> list[LsBlkNode]:
        state = getattr(host, "lsblk_devices", None)
        if state is not None:
            # SimulatedHost seam: read the in-memory state directly. Copy
            # each node including its nested children so callers never
            # mutate the simulated state.
            return _copy_lsblk_nodes(state)
        # Real transport: `lsblk -J` over asyncssh from the connection.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class Ipv4Address(TypedDict):
    """
    Attributes:
        address: The IPv4 address string, as the command printed it.
        mask_bits: The prefix length in bits, when the listing carried a
            mask token.
        netmask: The dotted-quad netmask, when the listing carried a mask
            token (a prefix length is expanded to its netmask).
        broadcast: The broadcast address, or None when the listing
            carried none.
        additional_ips: The alias addresses, as further ``Ipv4Address``
            entries in listing order; absent when the interface carries
            only one IPv4 address.
    """

    # The ``ipv4`` block of a ``NetworkDevice`` (and of each of its
    # ``additional_ips`` aliases), as a plain dict.
    #
    # ``mask_bits``/``netmask`` are NotRequired because the JSON branch
    # only attaches them when the addr entry carries a ``prefixlen``;
    # ``broadcast`` is always present on an IPv4 block but may be None.

    address: str
    mask_bits: NotRequired[int]
    netmask: NotRequired[str]
    broadcast: str | None
    additional_ips: NotRequired[list["Ipv4Address"]]


class Ipv6Address(TypedDict):
    """
    Attributes:
        address: The IPv6 address string, as the command printed it.
        mask_bits: The prefix length in bits, when the listing carried a
            mask token.
        additional_ips: The alias addresses, as further ``Ipv6Address``
            entries in listing order; absent when the interface carries
            only one IPv6 address.
    """

    # The ``ipv6`` block of a ``NetworkDevice`` (and of each of its
    # ``additional_ips`` aliases), as a plain dict.
    #
    # The IPv6 sibling of ``Ipv4Address`` without the IPv4-only
    # ``netmask``/``broadcast`` fields; ``mask_bits`` is NotRequired for
    # the same prefixlen-less JSON addr entries.

    address: str
    mask_bits: NotRequired[int]
    additional_ips: NotRequired[list["Ipv6Address"]]


class NetworkDevice(TypedDict):
    """
    Attributes:
        state: The interface state (``UP``/``DOWN``/``UNKNOWN``).
        ether: The MAC address, when the listing carried one.
        mtu: The interface MTU, when the listing carried one.
        ipv4: The primary IPv4 address block, when the interface carries
            an IPv4 address.
        ipv6: The primary IPv6 address block, when the interface carries
            an IPv6 address.
    """

    # One network interface's details (``NetworkDevices``' entry), as a
    # plain dict.
    #
    # A TypedDict rather than a class instance, like ``BlockDevice``:
    # entries are ordinary dicts, so equality against a literal dict
    # works and ``json.dumps`` serializes them directly. Only ``state``
    # is always present; the ether/mtu details and the per-family
    # ``Ipv4Address``/``Ipv6Address`` blocks are NotRequired because a
    # listing may carry none of them.

    state: str
    ether: NotRequired[str]
    mtu: NotRequired[int]
    ipv4: NotRequired[Ipv4Address]
    ipv6: NotRequired[Ipv6Address]


class NetworkDevices:
    """The network devices, as a dict of interface -> ``NetworkDevice`` dict.

    **Commands:**

    ```bash
    ip -j addr show 2> /dev/null || ip addr show 2> /dev/null || ifconfig -a
    ```

    **Returns:**

    ```python
    dict[str, NetworkDevice]
    ```

    Examples:
        Examples can be found in ``examples/modules/hardware/hardware_network_devices.py``.
    """

    # Mirrors pyinfra's ``hardware.NetworkDevices``: ``ip -j addr show``
    # (JSON) or, failing that, ``ip addr show`` / ``ifconfig -a`` (text)
    # lists the interfaces, and each maps to its ``NetworkDevice`` entry
    # -- the ``state`` (``UP``/``DOWN``/``UNKNOWN``), the optional ``ether``
    # MAC and ``mtu``, and an ``Ipv4Address``/``Ipv6Address`` block per
    # address family carrying the primary ``address`` plus, when present,
    # ``mask_bits``, ``netmask`` (IPv4 only), ``broadcast`` (IPv4 only,
    # possibly None) and any further addresses under ``additional_ips``,
    # for example::
    #
    #     {
    #         "enp1s0": {
    #             "ether": "12:34:56:78:9A:BC",
    #             "mtu": 1500,
    #             "state": "UP",
    #             "ipv4": {
    #                 "address": "192.168.1.100",
    #                 "mask_bits": 24,
    #                 "netmask": "255.255.255.0"
    #             },
    #             "ipv6": {
    #                 "address": "2001:db8:85a3::8a2e:370:7334",
    #                 "mask_bits": 64,
    #                 "additional_ips": [
    #                     {"address": "fe80::1234:5678:9abc:def0", "mask_bits": 64}
    #                 ]
    #             }
    #         },
    #         "wlo1": {
    #             "ether": "77:88:99:AA:BB:CC",
    #             "mtu": 1500,
    #             "state": "UNKNOWN"
    #         }
    #     }
    #
    # The ``Ipv4Addrs``/``Ipv6Addrs`` siblings are the address-only
    # projections of this dict. Mask tokens are normalized by pyinfra's
    # ``mask`` helper: a prefix length expands to its dotted-quad netmask,
    # a hex (``0xffffff00``) or dotted-quad netmask counts its bits. In the
    # text branch the state is a token heuristic (``UP`` or ``DOWN``
    # anywhere in the device section, else ``UNKNOWN``), device names keep
    # any ``@parent`` suffix, and a later section for the same name wins
    # outright; the JSON branch reads ``operstate`` and keeps an ``ether``
    # even for loopback. Addresses stay raw strings, without validation.
    # Empty or undecodable output reads as ``{}``, pyinfra's
    # ``default = dict``.
    #
    # Deviations from pyinfra: parse errors on unreadable entries are
    # skipped silently (pyinfra logs them through its logger), and non-dict
    # JSON devices or address entries are skipped rather than raising;
    # reemote5 hardens the parser.

    #: Shell used to gather the network device details over SSH: JSON
    #: first, then the plain-text `ip` and `ifconfig` fallbacks (pyinfra
    #: verbatim, the same command the Ipv4Addrs/Ipv6Addrs siblings run).
    COMMAND = "ip -j addr show 2> /dev/null || ip addr show 2> /dev/null || ifconfig -a"

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, NetworkDevice]:
        """Parse ``ip``/``ifconfig`` output into the device dict. Pure/testable.

        pyinfra's JSON sniff decides the branch: when the first two
        stripped lines start with ``[{`` the output is ``ip -j`` JSON,
        otherwise it is parsed as device sections of text.
        """
        lines = list(output)
        if "".join(line.strip() for line in lines[:2]).startswith("[{"):
            return _devices_from_json(lines)
        return _devices_from_text(lines)

    @classmethod
    async def process(cls, host: Host) -> dict[str, NetworkDevice]:
        state = getattr(host, "network_devices", None)
        if state is not None:
            # SimulatedHost seam: read the in-memory state directly. Copy
            # each device dict including its nested address blocks so
            # callers never mutate the simulated state.
            return {interface: _copy_device(info) for interface, info in state.items()}
        # Real transport: `ip`/`ifconfig` over asyncssh from the connection.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class SmbiosInfo:
    """The decoded SMBIOS/DMI tables, as a dict of section -> field dict.

    **Commands:**

    ```bash
    dmidecode -q 2> /dev/null || sudo -n dmidecode -q 2> /dev/null || true
    ```

    **Returns:**

    ```python
    dict[str, dict[str, str | list[str]]]
    ```

    Examples:
        Examples can be found in ``examples/modules/hardware/hardware_smbios_info.py``.
    """

    # A reemote5 original (pyinfra has no counterpart): ``dmidecode -q``
    # prints the firmware's SMBIOS structures as titled sections of
    # tab-indented ``Key: value`` rows, and each section maps its verbatim
    # title to its field dict, for example::
    #
    #     {
    #         "BIOS Information": {
    #             "Vendor": "Dell Inc.",
    #             "Version": "2.12.2",
    #             "Release Date": "07/09/2021",
    #         },
    #         "System Information": {
    #             "Manufacturer": "Dell Inc.",
    #             "Product Name": "PowerEdge R640",
    #             "Serial Number": "7XYZ123",
    #         },
    #         "Chassis Information": {
    #             "Manufacturer": "Dell Inc.",
    #             "Type": "Rack Mount Chassis",
    #         },
    #     }
    #
    # This is the privileged sibling of ``DmiInfo``: ``dmidecode`` usually
    # requires root (and the binary must be installed), but in exchange it
    # decodes the full tables -- the chassis ``Type`` arrives as a label
    # (``Rack Mount Chassis``, ``Notebook``, ``Other``, ...) rather than
    # ``DmiInfo``'s numeric code, and sections beyond the identity fields
    # (processor, memory, ports, ...) are included too. When the direct
    # read fails, the command retries through ``sudo -n dmidecode -q`` -- a
    # deliberate, documented deviation from "facts never elevate": ``-n``
    # never prompts, so the retry succeeds only for a root login or
    # passwordless (``NOPASSWD``) sudo, creates no askpass file and raises
    # no ``PrivilegeError``; the inventory's ``sudo_password`` is
    # deliberately NOT consulted (the library's askpass elevation belongs
    # to operations). The ``-q`` flag suppresses the handle/SMBIOS-version
    # preamble. A field whose value is
    # a list of deeper-indented items (e.g. ``Characteristics``) collects
    # them into a list of strings; every other value stays a raw string,
    # and a colon-bearing field with no value and no items reads as ``""``.
    # A repeated section title (``Memory Device`` appears once per DIMM)
    # keeps the last occurrence, the house last-wins rule -- the identity,
    # BIOS and chassis sections this fact targets each appear once. A
    # failed read (missing ``dmidecode``, no root or passwordless sudo, no
    # SMBIOS tables) is swallowed by the command and maps to ``{}``, the
    # house dict-fact fallback -- an empty dict cannot distinguish "no
    # tables" from "no access"; use ``DmiInfo`` for an unprivileged read.

    #: Shell used to gather the SMBIOS tables over SSH: quiet mode (no
    #: handle/version preamble), retried through passwordless ``sudo -n``
    #: when the direct read fails; a missing binary or an unreadable table
    #: is swallowed so the fact degrades to {}.
    COMMAND = (
        "dmidecode -q 2> /dev/null || sudo -n dmidecode -q 2> /dev/null || true"
    )

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, dict[str, str | list[str]]]:
        """Parse ``dmidecode -q`` sections into the field dicts. Pure/testable.

        A non-indented line opens a section keyed by its stripped title;
        a single-tab ``Key: value`` row adds a field (the value is the
        segment after the first colon); a deeper-indented row appends to
        the previous field's list, converting an empty string value to a
        list on the first item. Blank lines, colon-less field rows and
        indented rows before any section are skipped; a repeated section
        title keeps the last occurrence.
        """
        sections: dict[str, dict[str, str | list[str]]] = {}
        fields: dict[str, str | list[str]] | None = None
        last_key: str | None = None
        for line in output:
            if not line.strip():
                continue
            if not line.startswith("\t"):
                fields = {}
                sections[line.strip()] = fields
                last_key = None
                continue
            if fields is None:
                continue
            if line.startswith("\t\t"):
                if last_key is not None:
                    value = fields[last_key]
                    if not isinstance(value, list):
                        value = []
                        fields[last_key] = value
                    value.append(line.strip())
                continue
            key, sep, value = line.strip().partition(":")
            if not sep:
                continue
            last_key = key.strip()
            fields[last_key] = value.strip()
        return sections

    @classmethod
    async def process(cls, host: Host) -> dict[str, dict[str, str | list[str]]]:
        state = getattr(host, "smbios_info", None)
        if state is not None:
            # SimulatedHost seam: read the in-memory state directly. Copy
            # each section dict and any list values so callers never
            # mutate the simulated state.
            return {
                section: {
                    key: list(value) if isinstance(value, list) else value
                    for key, value in info.items()
                }
                for section, info in state.items()
            }
        # Real transport: `dmidecode -q` over asyncssh from the connection.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


__all__ = [
    "BlockDevice",
    "BlockDevices",
    "CpuInfo",
    "Cpus",
    "DiskStat",
    "DiskStats",
    "DmiInfo",
    "Ipv4Address",
    "Ipv4Addrs",
    "Ipv6Address",
    "Ipv6Addrs",
    "LsBlk",
    "LsBlkNode",
    "NetworkDevice",
    "NetworkDevices",
    "SmbiosInfo",
]