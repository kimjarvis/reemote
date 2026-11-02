# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the hardware module: ``BlockDevices``, ``CpuInfo``, ``Cpus``,
``DiskStats``, ``DmiInfo``, ``Ipv4Addrs``, ``Ipv6Addrs``, ``LsBlk``,
``NetworkDevices`` and ``SmbiosInfo``.

Covers ``reemote5.modules.hardware.hardware``: pyinfra's own fixtures over
the real-transport path, the documented example results, ``df`` parsing
edge cases (header and ``none`` rows, unmatched rows, repeated devices),
``lscpu`` parsing edge cases (flag lists, colon-less lines, the
second-colon truncation), ``getconf`` parsing edge cases (first-line-only,
non-integer output), ``/proc/diskstats`` parsing edge cases (the
discard/flush counter groups, short and non-integer rows, extra future
columns), sysfs DMI parsing edge cases (uevent/modalias skipping, empty
values, colons inside values), ``dmidecode -q`` parsing edge cases
(characteristic lists, duplicate sections, colon-less rows), ``lsblk -J``
parsing edge cases (nested children, unmounted
devices, malformed JSON, non-dict nodes), ``ip``/``ifconfig`` parsing
edge cases (the JSON sniff, alias ordering, other-family-only interfaces,
malformed JSON, duplicate sections, mask normalization, the state
heuristic), empty output, the ``SimulatedHost`` seams (read, default,
copy semantics) and the ``gather_fact`` fan-out across hosts.
"""

from reemote5.drivers import gather_fact
from reemote5.hosts import Host
from reemote5.modules import hardware
from reemote5.modules.hardware.hardware import (
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
from tests.simulated_host import SimulatedHost


class CannedShellHost(Host):
    """Returns fixed stdout from ``run_shell`` instead of connecting."""

    def __init__(self, stdout: str):
        super().__init__(name="canned")
        self._stdout = stdout
        self.shells: list[str] = []

    async def run_shell(self, shell: str, *, check: bool = False) -> str:
        self.shells.append(shell)
        return self._stdout


# --- BlockDevices --------------------------------------------------------------


async def test_block_devices_parses_pyintras_fixture():
    # pyinfra's own fact fixture (tests/facts/hardware.BlockDevices/devices.json):
    # the header row and the "none" pseudo-filesystem row are both skipped.
    host = CannedShellHost(
        """Filesystem                     1K-blocks      Used Available Use% Mounted on
/dev/disk1s1                   500 100 400  36% /
none                   500 100 400  36% /
"""
    )
    assert await BlockDevices.process(host) == {
        "/dev/disk1s1": {
            "blocks": "500",
            "used": "100",
            "available": "400",
            "used_percent": "36",
            "mount": "/",
        }
    }
    assert host.shells == ["df"]


def test_block_devices_parses_pyintras_documented_example():
    # pyinfra's docstring example result: /dev/sda1 mounted on /, all five
    # values kept as the raw strings df prints.
    assert BlockDevices.parse(["/dev/sda1 40325900 836392 39489508 3% /"]) == {
        "/dev/sda1": {
            "available": "39489508",
            "used_percent": "3",
            "mount": "/",
            "used": "836392",
            "blocks": "40325900",
        }
    }


def test_block_devices_parse_skips_headers_and_unmatched_rows():
    # The header, blank lines and rows whose device or mount point carries
    # a character outside the regex's classes (an NFS host:/export source,
    # a dotted device name) never match, exactly like pyinfra.
    assert (
        BlockDevices.parse(
            [
                "Filesystem     1K-blocks  Used Available Use% Mounted on",
                "",
                "files.example.com:/export 100 20 80 20% /mnt/nfs",
                "not a df row",
            ]
        )
        == {}
    )


def test_block_devices_parse_skips_none_but_keeps_tmpfs():
    # Only the literal "none" device is filtered; other pseudo-filesystems
    # (tmpfs) and a 100%-full device are reported like any other row.
    assert BlockDevices.parse(
        [
            "none 500 100 400 36% /dev/shm",
            "tmpfs 100 0 100 0% /run",
            "/dev/sda2 200 200 0 100% /boot",
        ]
    ) == {
        "tmpfs": {
            "blocks": "100",
            "used": "0",
            "available": "100",
            "used_percent": "0",
            "mount": "/run",
        },
        "/dev/sda2": {
            "blocks": "200",
            "used": "200",
            "available": "0",
            "used_percent": "100",
            "mount": "/boot",
        },
    }


def test_block_devices_parse_keeps_the_last_duplicate_row():
    assert BlockDevices.parse(
        [
            "/dev/sda1 500 100 400 36% /",
            "/dev/sda1 500 200 300 40% /",
        ]
    ) == {
        "/dev/sda1": {
            "blocks": "500",
            "used": "200",
            "available": "300",
            "used_percent": "40",
            "mount": "/",
        }
    }


async def test_block_devices_is_empty_on_empty_output():
    # A failed read maps to {} (pyinfra's ``default = dict``); a header-only
    # listing has no matching rows either.
    assert await BlockDevices.process(CannedShellHost("")) == {}
    assert (
        await BlockDevices.process(
            CannedShellHost(
                "Filesystem     1K-blocks  Used Available Use% Mounted on\n"
            )
        )
        == {}
    )


async def test_block_devices_reads_the_simulated_seam():
    devices = {
        "/dev/sda1": {
            "blocks": "500",
            "used": "100",
            "available": "400",
            "used_percent": "36",
            "mount": "/",
        }
    }
    host = SimulatedHost(name="h", block_devices=devices)
    assert await BlockDevices.process(host) == devices
    # The seam defaults to {}: a simulated machine with no devices listed.
    assert await BlockDevices.process(SimulatedHost(name="h")) == {}


async def test_block_devices_seam_returns_a_copy():
    host = SimulatedHost(
        name="h",
        block_devices={
            "/dev/sda1": {
                "blocks": "500",
                "used": "100",
                "available": "400",
                "used_percent": "36",
                "mount": "/",
            }
        },
    )
    devices = await BlockDevices.process(host)
    devices["/dev/sda1"]["used"] = "500"
    devices["/dev/sdb1"] = {
        "blocks": "100",
        "used": "0",
        "available": "100",
        "used_percent": "0",
        "mount": "/data",
    }
    assert host.block_devices == {
        "/dev/sda1": {
            "blocks": "500",
            "used": "100",
            "available": "400",
            "used_percent": "36",
            "mount": "/",
        }
    }


async def test_block_devices_gather_fact_across_hosts():
    hosts = [
        SimulatedHost(
            name="web1",
            block_devices={
                "/dev/sda1": {
                    "blocks": "500",
                    "used": "100",
                    "available": "400",
                    "used_percent": "36",
                    "mount": "/",
                }
            },
        ),
        SimulatedHost(name="web2"),
    ]
    assert await gather_fact(hosts, BlockDevices) == {
        "web1": {
            "/dev/sda1": {
                "blocks": "500",
                "used": "100",
                "available": "400",
                "used_percent": "36",
                "mount": "/",
            }
        },
        "web2": {},
    }


def test_block_devices_is_exported_through_the_namespace():
    # Both import paths resolve to the same class: hardware.BlockDevices
    # through the package and BlockDevices through the module source.
    assert hardware.BlockDevices is BlockDevices
    assert "BlockDevices" in hardware.__all__


# --- CpuInfo -------------------------------------------------------------------


async def test_cpu_info_parses_pyintras_fixture():
    # pyinfra's own fact fixture (tests/facts/hardware.CpuInfo/1cpu.json):
    # every "Key: value" row becomes a dict entry, and the Flags row splits
    # into a list of tokens. The Itlb multihit row carries trailing padding
    # in the fixture, which parse strips (so it is omitted here).
    host = CannedShellHost(
        """Architecture:                    x86_64
CPU op-mode(s):                  32-bit, 64-bit
Address sizes:                   36 bits physical, 48 bits virtual
Byte Order:                      Little Endian
CPU(s):                          4
On-line CPU(s) list:             0-3
Vendor ID:                       GenuineIntel
Model name:                      Intel(R) Atom(TM) CPU N2800   @ 1.86GHz
CPU family:                      6
Model:                           54
Thread(s) per core:              2
Core(s) per socket:              2
Socket(s):                       1
Stepping:                        1
CPU(s) scaling MHz:              48%
CPU max MHz:                     1862,0000
CPU min MHz:                     798,0000
BogoMIPS:                        3735,20
Flags:                           fpu vme de pse tsc msr pae mce cx8 apic sep mtrr pge mca cmov pat pse36 clflush dts acpi mmx fxsr sse sse2 ss ht tm pbe syscall nx lm constant_tsc arch_perfmon pebs bts nopl nonstop_tsc cpuid aperfmperf pni dtes64 monitor ds_cpl est tm2 ssse3 cx16 xt pr pdcm movbe lahf_lm dtherm arat
L1d cache:                       48 KiB (2 instances)
L1i cache:                       64 KiB (2 instances)
L2 cache:                        1 MiB (2 instances)
NUMA node(s):                    1
NUMA node0 CPU(s):               0-3
Vulnerability Itlb multihit:     Not affected
Vulnerability L1tf:              Not affected
Vulnerability Mds:               Not affected
Vulnerability Meltdown:          Not affected
Vulnerability Spec store bypass: Not affected
Vulnerability Spectre v1:        Not affected
Vulnerability Spectre v2:        Not affected
Vulnerability Srbds:             Not affected
Vulnerability Tsx async abort:   Not affected
"""
    )
    assert await CpuInfo.process(host) == {
        "Architecture": "x86_64",
        "CPU op-mode(s)": "32-bit, 64-bit",
        "Address sizes": "36 bits physical, 48 bits virtual",
        "Byte Order": "Little Endian",
        "CPU(s)": "4",
        "On-line CPU(s) list": "0-3",
        "Vendor ID": "GenuineIntel",
        "Model name": "Intel(R) Atom(TM) CPU N2800   @ 1.86GHz",
        "CPU family": "6",
        "Model": "54",
        "Thread(s) per core": "2",
        "Core(s) per socket": "2",
        "Socket(s)": "1",
        "Stepping": "1",
        "CPU(s) scaling MHz": "48%",
        "CPU max MHz": "1862,0000",
        "CPU min MHz": "798,0000",
        "BogoMIPS": "3735,20",
        "Flags": [
            "fpu", "vme", "de", "pse", "tsc", "msr", "pae", "mce", "cx8",
            "apic", "sep", "mtrr", "pge", "mca", "cmov", "pat", "pse36",
            "clflush", "dts", "acpi", "mmx", "fxsr", "sse", "sse2", "ss",
            "ht", "tm", "pbe", "syscall", "nx", "lm", "constant_tsc",
            "arch_perfmon", "pebs", "bts", "nopl", "nonstop_tsc", "cpuid",
            "aperfmperf", "pni", "dtes64", "monitor", "ds_cpl", "est", "tm2",
            "ssse3", "cx16", "xt", "pr", "pdcm", "movbe", "lahf_lm",
            "dtherm", "arat",
        ],
        "L1d cache": "48 KiB (2 instances)",
        "L1i cache": "64 KiB (2 instances)",
        "L2 cache": "1 MiB (2 instances)",
        "NUMA node(s)": "1",
        "NUMA node0 CPU(s)": "0-3",
        "Vulnerability Itlb multihit": "Not affected",
        "Vulnerability L1tf": "Not affected",
        "Vulnerability Mds": "Not affected",
        "Vulnerability Meltdown": "Not affected",
        "Vulnerability Spec store bypass": "Not affected",
        "Vulnerability Spectre v1": "Not affected",
        "Vulnerability Spectre v2": "Not affected",
        "Vulnerability Srbds": "Not affected",
        "Vulnerability Tsx async abort": "Not affected",
    }
    assert host.shells == ["LANG=C lscpu"]


def test_cpu_info_parses_pyintras_documented_example():
    # The class docstring's representative example: string values stay raw
    # strings (inner spacing preserved), the Flags row becomes a token list.
    assert CpuInfo.parse(
        [
            "Architecture:                    x86_64",
            "CPU(s):                          4",
            "Vendor ID:                       GenuineIntel",
            "Model name:                      Intel(R) Atom(TM) CPU N2800   @ 1.86GHz",
            "Flags:                           fpu vme de pse tsc msr",
        ]
    ) == {
        "Architecture": "x86_64",
        "CPU(s)": "4",
        "Vendor ID": "GenuineIntel",
        "Model name": "Intel(R) Atom(TM) CPU N2800   @ 1.86GHz",
        "Flags": ["fpu", "vme", "de", "pse", "tsc", "msr"],
    }


def test_cpu_info_parse_splits_flag_keys_into_lists():
    # Any key containing "flag" (case-insensitively) splits its value into a
    # list; a non-flag key keeps its value a raw string even with spaces.
    assert CpuInfo.parse(
        [
            "Flags: fpu vme de",
            "CPU flags: a b c",
            "Model name: Intel(R) Atom(TM)",
        ]
    ) == {
        "Flags": ["fpu", "vme", "de"],
        "CPU flags": ["a", "b", "c"],
        "Model name": "Intel(R) Atom(TM)",
    }


def test_cpu_info_parse_skips_colonless_lines():
    # Blank lines and stray output without a colon are skipped (pyinfra
    # would raise IndexError on them; reemote5 hardens the parser).
    assert CpuInfo.parse(
        ["", "no colon here", "   ", "Architecture: x86_64"]
    ) == {"Architecture": "x86_64"}


def test_cpu_info_parse_truncates_at_the_second_colon():
    # Mirrors pyinfra's split(":")[1]: only the segment after the FIRST
    # colon is kept, so a second colon in a value truncates it.
    assert CpuInfo.parse(["Key: first: second"]) == {"Key": "first"}


async def test_cpu_info_is_none_on_empty_output():
    # pyinfra's invalid fixture (output null -> fact null): a failed/empty
    # read parses to None, as does output with no colon-bearing rows.
    assert await CpuInfo.process(CannedShellHost("")) is None
    assert CpuInfo.parse([]) is None
    assert CpuInfo.parse(["", "  "]) is None


async def test_cpu_info_reads_the_simulated_seam():
    info = {"Architecture": "x86_64", "Flags": ["fpu", "vme"]}
    host = SimulatedHost(name="h", cpu_info=info)
    assert await CpuInfo.process(host) == info
    # The seam defaults to {}: a simulated machine reporting no lscpu rows.
    assert await CpuInfo.process(SimulatedHost(name="h")) == {}


async def test_cpu_info_seam_returns_a_copy():
    host = SimulatedHost(
        name="h",
        cpu_info={"Architecture": "x86_64", "Flags": ["fpu", "vme"]},
    )
    info = await CpuInfo.process(host)
    info["Architecture"] = "aarch64"
    info["Flags"].append("de")
    info["CPU(s)"] = "8"
    assert host.cpu_info == {"Architecture": "x86_64", "Flags": ["fpu", "vme"]}


async def test_cpu_info_gather_fact_across_hosts():
    hosts = [
        SimulatedHost(
            name="web1",
            cpu_info={"Architecture": "x86_64", "Flags": ["fpu", "vme"]},
        ),
        SimulatedHost(name="web2"),
    ]
    assert await gather_fact(hosts, CpuInfo) == {
        "web1": {"Architecture": "x86_64", "Flags": ["fpu", "vme"]},
        "web2": {},
    }


def test_cpu_info_is_exported_through_the_namespace():
    # Both import paths resolve to the same class: hardware.CpuInfo through
    # the package and CpuInfo through the module source.
    assert hardware.CpuInfo is CpuInfo
    assert "CpuInfo" in hardware.__all__


# --- Cpus ----------------------------------------------------------------------


async def test_cpus_parses_pyintras_valid_fixture():
    # pyinfra's own fact fixture (tests/facts/hardware.Cpus/valid.json):
    # getconf prints "1", which parses to the int 1.
    host = CannedShellHost("1\n")
    assert await Cpus.process(host) == 1
    assert host.shells == [
        "getconf NPROCESSORS_ONLN 2> /dev/null || getconf _NPROCESSORS_ONLN"
    ]


async def test_cpus_parses_pyintras_invalid_fixture():
    # pyinfra's own fact fixture (tests/facts/hardware.Cpus/invalid.json):
    # a non-integer answer ("nope") parses to None, not an error.
    assert await Cpus.process(CannedShellHost("nope\n")) is None


def test_cpus_parse_ignores_lines_after_the_first():
    # Mirrors pyinfra's int(list(output)[0]): only the first line is
    # converted; anything after it is ignored.
    assert Cpus.parse(["4", "8", "16"]) == 4


def test_cpus_parse_tolerates_surrounding_whitespace():
    # int() itself strips surrounding whitespace, so a padded count still
    # converts (pyinfra passes the raw line to int too).
    assert Cpus.parse([" 4 "]) == 4


def test_cpus_parse_is_none_when_the_first_line_is_not_an_integer():
    # The ValueError catch fires on the FIRST line (here blank), exactly
    # like pyinfra -- a later integer line is never reached.
    assert Cpus.parse(["", "4"]) is None
    assert Cpus.parse(["1.5"]) is None


async def test_cpus_is_none_on_empty_output():
    # Empty output (a failed read) reads as None -- pyinfra answers with
    # its default() fallback of None when the command prints nothing.
    assert await Cpus.process(CannedShellHost("")) is None
    assert Cpus.parse([]) is None


async def test_cpus_reads_the_simulated_seam():
    host = SimulatedHost(name="h", cpus=8)
    assert await Cpus.process(host) == 8
    # The seam defaults to pyinfra's fixture value (1): a single-CPU
    # simulated machine, never falling through to the shell.
    assert await Cpus.process(SimulatedHost(name="h")) == 1


async def test_cpus_gather_fact_across_hosts():
    hosts = [
        SimulatedHost(name="web1", cpus=4),
        SimulatedHost(name="web2"),
    ]
    assert await gather_fact(hosts, Cpus) == {"web1": 4, "web2": 1}


def test_cpus_is_exported_through_the_namespace():
    # Both import paths resolve to the same class: hardware.Cpus through
    # the package and Cpus through the module source.
    assert hardware.Cpus is Cpus
    assert "Cpus" in hardware.__all__


# --- DiskStats -----------------------------------------------------------------


async def test_disk_stats_parses_classic_and_modern_rows():
    # A classic 2.6-era row (eleven counters) beside a 5.5+ row carrying
    # the discard and flush groups; both are kept, mounted or not.
    host = CannedShellHost(
        """   8       0 sda 12345 678 901234 5678 23456 7890 1234567 8901 0 12345 14567
 259       0 nvme0n1 100 10 2000 30 400 50 4000 60 0 70 90 1 2 300 4 5 6
"""
    )
    assert await DiskStats.process(host) == {
        "sda": {
            "reads_completed": 12345,
            "reads_merged": 678,
            "sectors_read": 901234,
            "ms_reading": 5678,
            "writes_completed": 23456,
            "writes_merged": 7890,
            "sectors_written": 1234567,
            "ms_writing": 8901,
            "ios_in_progress": 0,
            "ms_doing_io": 12345,
            "weighted_ms_doing_io": 14567,
        },
        "nvme0n1": {
            "reads_completed": 100,
            "reads_merged": 10,
            "sectors_read": 2000,
            "ms_reading": 30,
            "writes_completed": 400,
            "writes_merged": 50,
            "sectors_written": 4000,
            "ms_writing": 60,
            "ios_in_progress": 0,
            "ms_doing_io": 70,
            "weighted_ms_doing_io": 90,
            "discards_completed": 1,
            "discards_merged": 2,
            "sectors_discarded": 300,
            "ms_discarding": 4,
            "flush_requests": 5,
            "ms_flushing": 6,
        },
    }
    assert host.shells == ["cat /proc/diskstats"]


def test_disk_stats_parse_adds_the_discard_group_on_4_18_rows():
    # An 18-field row (kernel 4.18..5.4) carries the four discard counters
    # but no flush group.
    assert DiskStats.parse(["   8      16 sdb 11 1 22 3 44 5 66 7 0 8 9 2 3 40 4"]) == {
        "sdb": {
            "reads_completed": 11,
            "reads_merged": 1,
            "sectors_read": 22,
            "ms_reading": 3,
            "writes_completed": 44,
            "writes_merged": 5,
            "sectors_written": 66,
            "ms_writing": 7,
            "ios_in_progress": 0,
            "ms_doing_io": 8,
            "weighted_ms_doing_io": 9,
            "discards_completed": 2,
            "discards_merged": 3,
            "sectors_discarded": 40,
            "ms_discarding": 4,
        }
    }


def test_disk_stats_parse_ignores_columns_beyond_the_flush_group():
    # A future kernel appending more columns does not change the parsed
    # counters: everything past the flush group is ignored.
    row = "   8       0 sda 1 2 3 4 5 6 7 8 0 9 10 1 2 3 4 5 6"
    assert DiskStats.parse([row + " 99 99"]) == DiskStats.parse([row])


def test_disk_stats_parse_skips_short_and_non_integer_rows():
    # Blank lines, rows without the full eleven classic counters and rows
    # whose counters are not all integers never contribute a device.
    assert (
        DiskStats.parse(
            [
                "",
                "8 0 sda 1 2 3",
                "8 0 sdb 1 2 3 4 5 6 7 8 0 1 nope",
                "not a diskstats row at all",
            ]
        )
        == {}
    )


def test_disk_stats_parse_keeps_the_last_duplicate_row():
    assert DiskStats.parse(
        [
            "   8       0 sda 1 0 10 1 2 0 20 2 0 3 3",
            "   8       0 sda 5 0 50 5 6 0 60 6 0 7 7",
        ]
    ) == {
        "sda": {
            "reads_completed": 5,
            "reads_merged": 0,
            "sectors_read": 50,
            "ms_reading": 5,
            "writes_completed": 6,
            "writes_merged": 0,
            "sectors_written": 60,
            "ms_writing": 6,
            "ios_in_progress": 0,
            "ms_doing_io": 7,
            "weighted_ms_doing_io": 7,
        }
    }


async def test_disk_stats_is_empty_on_empty_output():
    # A failed read maps to {}, the house dict-fact fallback.
    assert await DiskStats.process(CannedShellHost("")) == {}
    assert DiskStats.parse([]) == {}


async def test_disk_stats_reads_the_simulated_seam():
    stats = {
        "sda": {
            "reads_completed": 12345,
            "reads_merged": 678,
            "sectors_read": 901234,
            "ms_reading": 5678,
            "writes_completed": 23456,
            "writes_merged": 7890,
            "sectors_written": 1234567,
            "ms_writing": 8901,
            "ios_in_progress": 0,
            "ms_doing_io": 12345,
            "weighted_ms_doing_io": 14567,
        }
    }
    host = SimulatedHost(name="h", disk_stats=stats)
    assert await DiskStats.process(host) == stats
    # The seam defaults to {}: a simulated machine with no rows listed.
    assert await DiskStats.process(SimulatedHost(name="h")) == {}


async def test_disk_stats_seam_returns_a_copy():
    host = SimulatedHost(name="h", disk_stats={"sda": {"reads_completed": 10}})
    stats = await DiskStats.process(host)
    stats["sda"]["reads_completed"] = 99
    stats["sdb"] = {"reads_completed": 1}
    assert host.disk_stats == {"sda": {"reads_completed": 10}}


async def test_disk_stats_gather_fact_across_hosts():
    hosts = [
        SimulatedHost(name="web1", disk_stats={"sda": {"reads_completed": 10}}),
        SimulatedHost(name="web2"),
    ]
    assert await gather_fact(hosts, DiskStats) == {
        "web1": {"sda": {"reads_completed": 10}},
        "web2": {},
    }


def test_disk_stats_is_exported_through_the_namespace():
    # Both import paths resolve to the same class: hardware.DiskStats
    # through the package and DiskStats through the module source.
    assert hardware.DiskStats is DiskStats
    assert "DiskStats" in hardware.__all__


# --- DmiInfo -------------------------------------------------------------------


async def test_dmi_info_parses_a_sysfs_dump():
    # A realistic grep -H dump of /sys/class/dmi/id/ on a Dell server: each
    # row maps its file name to the raw sysfs value, chassis_type included
    # as the numeric SMBIOS code.
    host = CannedShellHost(
        """/sys/class/dmi/id/bios_date:07/09/2021
/sys/class/dmi/id/bios_vendor:Dell Inc.
/sys/class/dmi/id/bios_version:2.12.2
/sys/class/dmi/id/board_name:08HT8T
/sys/class/dmi/id/board_vendor:Dell Inc.
/sys/class/dmi/id/chassis_type:23
/sys/class/dmi/id/chassis_vendor:Dell Inc.
/sys/class/dmi/id/product_family:PowerEdge
/sys/class/dmi/id/product_name:PowerEdge R640
/sys/class/dmi/id/product_serial:7XYZ123
/sys/class/dmi/id/sys_vendor:Dell Inc.
"""
    )
    assert await DmiInfo.process(host) == {
        "bios_date": "07/09/2021",
        "bios_vendor": "Dell Inc.",
        "bios_version": "2.12.2",
        "board_name": "08HT8T",
        "board_vendor": "Dell Inc.",
        "chassis_type": "23",
        "chassis_vendor": "Dell Inc.",
        "product_family": "PowerEdge",
        "product_name": "PowerEdge R640",
        "product_serial": "7XYZ123",
        "sys_vendor": "Dell Inc.",
    }
    assert host.shells == ["grep -H '' /sys/class/dmi/id/* 2> /dev/null || true"]


def test_dmi_info_parse_skips_uevent_modalias_and_foreign_lines():
    # The directory's uevent/modalias entries are not identity fields, and
    # any line outside the /sys/class/dmi/id/ prefix (or without a colon)
    # never contributes -- a grep error leaked to stdout cannot pollute.
    assert DmiInfo.parse(
        [
            "/sys/class/dmi/id/uevent:MODALIAS=dmi:bvnDellInc.:bvr2.12.2",
            "/sys/class/dmi/id/modalias:dmi:bvnDellInc.:bvr2.12.2",
            "/sys/firmware/dmi/tables/DMI:binary",
            "grep: /sys/class/dmi/id/power: Is a directory",
            "/sys/class/dmi/id/sys_vendor:Dell Inc.",
        ]
    ) == {"sys_vendor": "Dell Inc."}


def test_dmi_info_parse_skips_empty_values_and_keeps_inner_colons():
    # An unset firmware field reads as an empty file (grep prints just the
    # path and colon) and is skipped; a colon inside a value survives
    # because only the first colon splits path from value.
    assert DmiInfo.parse(
        [
            "/sys/class/dmi/id/product_sku:",
            "/sys/class/dmi/id/bios_version:2.12.2:beta",
            "/sys/class/dmi/id/board_name:   ",
        ]
    ) == {"bios_version": "2.12.2:beta"}


async def test_dmi_info_is_empty_on_empty_output():
    # A machine without DMI (a container, many ARM boards/VMs) has no
    # /sys/class/dmi/id/ directory: the swallowed failed read maps to {},
    # the house dict-fact fallback.
    assert await DmiInfo.process(CannedShellHost("")) == {}


async def test_dmi_info_reads_the_simulated_seam():
    info = {"sys_vendor": "QEMU", "product_name": "Standard PC (Q35 + ICH9, 2009)"}
    host = SimulatedHost(name="h", dmi_info=info)
    assert await DmiInfo.process(host) == info
    # The seam defaults to {}: a simulated machine with no DMI identity.
    assert await DmiInfo.process(SimulatedHost(name="h")) == {}


async def test_dmi_info_seam_returns_a_copy():
    host = SimulatedHost(name="h", dmi_info={"sys_vendor": "Dell Inc."})
    info = await DmiInfo.process(host)
    info["sys_vendor"] = "HPE"
    info["product_name"] = "ProLiant DL380"
    assert host.dmi_info == {"sys_vendor": "Dell Inc."}


async def test_dmi_info_gather_fact_across_hosts():
    hosts = [
        SimulatedHost(name="web1", dmi_info={"chassis_type": "23"}),
        SimulatedHost(name="web2"),
    ]
    assert await gather_fact(hosts, DmiInfo) == {
        "web1": {"chassis_type": "23"},
        "web2": {},
    }


def test_dmi_info_is_exported_through_the_namespace():
    # Both import paths resolve to the same class: hardware.DmiInfo
    # through the package and DmiInfo through the module source.
    assert hardware.DmiInfo is DmiInfo
    assert "DmiInfo" in hardware.__all__


# --- Ipv4Addrs -----------------------------------------------------------------


async def test_ipv4_addrs_parses_pyintras_fixture():
    # pyinfra's own fact fixture
    # (tests/facts/hardware.Ipv4Addrs/linux_ip_multiple.json): text
    # `ip addr show` output. eth0 carries a primary and an alias, kept in
    # listing order with no octet validation (166.22.203.666 is pyinfra's
    # own value); eth1 has only IPv6 and is omitted. The fixture's
    # trailing padding on the inet6 rows is stripped by parse.
    host = CannedShellHost(
        """2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc fq_codel state UP group default qlen 1000
link/ether c6:c5:60:cd:73:f1 brd ff:ff:ff:ff:ff:ff
inet 166.22.203.666/20 brd 165.22.207.255 scope global eth0
inet 10.18.0.10/16 brd 10.18.255.255 scope global eth0
inet6 fe80::c4c5:60ff:fecd:73f1/64 scope link
3: eth1: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc fq_codel state UP group default qlen 1000
link/ether c6:c5:60:cd:73:f1 brd ff:ff:ff:ff:ff:ff
inet6 fe80::c4c5:60ff:fecd:73f1/64 scope link
"""
    )
    assert await Ipv4Addrs.process(host) == {"eth0": ["166.22.203.666", "10.18.0.10"]}
    assert host.shells == [
        "ip -j addr show 2> /dev/null || ip addr show 2> /dev/null || ifconfig -a"
    ]


def test_ipv4_addrs_parses_pyintras_documented_example():
    # pyinfra's docstring example result: eth0 -> ["127.0.0.1"].
    assert Ipv4Addrs.parse(
        [
            "1: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc fq_codel state UP group default qlen 1000",
            "inet 127.0.0.1/8 scope host eth0",
        ]
    ) == {"eth0": ["127.0.0.1"]}


async def test_ipv4_addrs_parses_pyintras_json_fixture():
    # pyinfra's NetworkDevices fixture
    # (tests/facts/hardware.NetworkDevices/linux_ip_json.json), verbatim:
    # `ip -j addr show` JSON with one inet and one inet6 entry -- only the
    # inet address survives the IPv4 projection.
    host = CannedShellHost(
        """[
  {
    "ifindex": 2,
    "ifname": "eth0",
    "flags": [
      "BROADCAST",
      "MULTICAST",
      "UP",
      "LOWER_UP"
    ],
    "mtu": 1500,
    "qdisc": "fq_codel",
    "operstate": "UP",
    "group": "default",
    "txqlen": 1000,
    "link_type": "ether",
    "address": "08:00:27:6d:95:c4",
    "broadcast": "ff:ff:ff:ff:ff:ff",
    "altnames": [
      "enx0800276d95c4"
    ],
    "addr_info": [
      {
        "family": "inet",
        "local": "192.168.1.2",
        "prefixlen": 24,
        "broadcast": "192.168.1.255",
        "scope": "global",
        "dynamic": true,
        "noprefixroute": true,
        "label": "eth0",
        "valid_life_time": 31856,
        "preferred_life_time": 31856
      },
      {
        "family": "inet6",
        "local": "2a01::01",
        "prefixlen": 64,
        "scope": "global",
        "dynamic": true,
        "noprefixroute": true,
        "valid_life_time": 28879,
        "preferred_life_time": 28879
      }
    ]
  }
]
"""
    )
    assert await Ipv4Addrs.process(host) == {"eth0": ["192.168.1.2"]}


def test_ipv4_addrs_json_keeps_aliases_in_listing_order():
    # Condensed from pyinfra's NetworkDevices fixture
    # (tests/facts/hardware.NetworkDevices/linux_ip_json_multiple_addrs.json):
    # two inet entries -- the primary first, then the alias pyinfra files
    # under additional_ips, in listing order.
    assert Ipv4Addrs.parse(
        [
            "[",
            "  {",
            '    "ifname": "eth0",',
            '    "addr_info": [',
            '      {"family": "inet", "local": "166.22.203.66", "prefixlen": 20},',
            '      {"family": "inet", "local": "10.18.0.10", "prefixlen": 16},',
            '      {"family": "inet6", "local": "fe80::c4c5:60ff:fecd:73f1"}',
            "    ]",
            "  }",
            "]",
        ]
    ) == {"eth0": ["166.22.203.66", "10.18.0.10"]}


async def test_ipv4_addrs_parses_pyintras_ifconfig_fixture():
    # pyinfra's NetworkDevices fixture
    # (tests/facts/hardware.NetworkDevices/linux_ifconfig.json), verbatim:
    # the ifconfig fallback shape (netmask token, no CIDR slash). lo's row
    # carries no broadcast and still matches.
    host = CannedShellHost(
        """enp1s0: flags=4163<UP,BROADCAST,RUNNING,MULTICAST>  mtu 1500
        inet 192.168.123.111  netmask 255.255.255.0  broadcast 192.168.123.255
        inet6 2a01:e0a:5c2:7450:b241:6fff:fe0a:cf22  prefixlen 64  scopeid 0x0<global>
        inet6 fe80::b241:6fff:fe0a:cf22  prefixlen 64  scopeid 0x20<link>
        ether b0:41:6f:0a:cf:22  txqueuelen 1000  (Ethernet)
        RX packets 52490  bytes 16827945 (16.0 MiB)
        RX errors 0  dropped 0  overruns 0  frame 0
        TX packets 21304  bytes 2948965 (2.8 MiB)
        TX errors 0  dropped 0 overruns 0  carrier 0  collisions 0
incusbr0: flags=4163<UP,BROADCAST,RUNNING,MULTICAST>  mtu 1500
        inet 10.232.181.1  netmask 255.255.255.0  broadcast 0.0.0.0
        inet6 fe80::216:3eff:fe9c:8200  prefixlen 64  scopeid 0x20<link>
        inet6 fd42:5bce:bd73:eda6::1  prefixlen 64  scopeid 0x0<global>
        ether 00:16:3e:9c:82:00  txqueuelen 1000  (Ethernet)
        RX packets 2592  bytes 253292 (247.3 KiB)
        RX errors 0  dropped 0  overruns 0  frame 0
        TX packets 1289  bytes 185014 (180.6 KiB)
        TX errors 0  dropped 0 overruns 0  carrier 0  collisions 0
lo: flags=73<UP,LOOPBACK,RUNNING>  mtu 65536
        inet 127.0.0.1  netmask 255.0.0.0
        inet6 ::1  prefixlen 128  scopeid 0x10<host>
        loop  txqueuelen 1000  (Local Loopback)
        RX packets 34  bytes 2816 (2.7 KiB)
        RX errors 0  dropped 0  overruns 0  frame 0
        TX packets 34  bytes 2816 (2.7 KiB)
        TX errors 0  dropped 0 overruns 0  carrier 0  collisions 0
"""
    )
    assert await Ipv4Addrs.process(host) == {
        "enp1s0": ["192.168.123.111"],
        "incusbr0": ["10.232.181.1"],
        "lo": ["127.0.0.1"],
    }


def test_ipv4_addrs_parses_pyintras_macos_ifconfig_fixture():
    # pyinfra's NetworkDevices fixture
    # (tests/facts/hardware.NetworkDevices/macos_ifconfig.json), verbatim:
    # the macOS ifconfig shape with a hex netmask (0xffffff00).
    assert Ipv4Addrs.parse(
        [
            "en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500",
            "inet 1.2.3.4 netmask 0xffffff00 broadcast 255.255.255.255",
            "inet6 2a01::01 prefixlen 64 autoconf temporary",
            "nd6 options=201<PERFORMNUD,DAD>",
            "media: autoselect",
            "status: active",
        ]
    ) == {"en0": ["1.2.3.4"]}


async def test_ipv4_addrs_is_empty_on_empty_or_malformed_output():
    # pyinfra's NetworkDevices empty/noise fixtures: empty output, an
    # empty JSON list, a JSON list holding one empty dict (no ifname), and
    # line noise that fails the JSON sniff and matches no device section.
    assert await Ipv4Addrs.process(CannedShellHost("")) == {}
    assert Ipv4Addrs.parse([]) == {}
    assert Ipv4Addrs.parse(["[]"]) == {}
    assert Ipv4Addrs.parse(["[{", "}]"]) == {}
    assert Ipv4Addrs.parse(["[%^&$*&^%(*&%^&%#$"]) == {}


def test_ipv4_addrs_json_skips_unusable_entries():
    # A device without an ifname is dropped (pyinfra's missing_ifname
    # fixture); addr entries without a local or a family are skipped, and
    # inet6 entries never enter the IPv4 projection.
    assert Ipv4Addrs.parse(
        ['[{"addr_info": [{"family": "inet", "local": "10.0.0.1"}]}]']
    ) == {}
    assert Ipv4Addrs.parse(
        [
            '[{"ifname": "eth0", "addr_info": [{"family": "inet"}, {"local": "10.0.0.2"}, {"family": "inet6", "local": "fe80::1"}, {"family": "inet", "local": "10.0.0.1"}]}]'
        ]
    ) == {"eth0": ["10.0.0.1"]}
    # Non-dict devices or addr entries are skipped rather than raising
    # (pyinfra's walker would crash; reemote5 hardens the parser). The
    # first element stays a dict so the JSON sniff still fires.
    assert Ipv4Addrs.parse(
        ['[{"ifname": "eth0", "addr_info": [5, {"family": "inet", "local": "10.0.0.1"}]}, 7]']
    ) == {"eth0": ["10.0.0.1"]}


def test_ipv4_addrs_json_drops_an_empty_primary_address():
    # pyinfra's projection skips an interface whose primary address is
    # empty -- even when a usable alias follows it.
    assert Ipv4Addrs.parse(
        '[{"ifname": "eth0", "addr_info": ['
        '{"family": "inet", "local": ""}, '
        '{"family": "inet", "local": "10.0.0.1"}]}]'
    ) == {}


def test_ipv4_addrs_text_keeps_the_last_duplicate_section():
    # A later section for the same interface wins, like pyinfra's device
    # map -- including dropping the interface when the last section
    # carries no IPv4 address.
    assert Ipv4Addrs.parse(
        [
            "2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 state UP",
            "inet 10.0.0.1/24 brd 10.0.0.255 scope global eth0",
            "3: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 state UP",
            "inet 10.0.0.2/24 brd 10.0.0.255 scope global eth0",
        ]
    ) == {"eth0": ["10.0.0.2"]}
    assert Ipv4Addrs.parse(
        [
            "2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 state UP",
            "inet 10.0.0.1/24 brd 10.0.0.255 scope global eth0",
            "3: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 state UP",
            "inet6 fe80::1/64 scope link",
        ]
    ) == {}


def test_ipv4_addrs_ifconfig_rows_without_a_netmask_never_match():
    # The ifconfig regex requires the netmask token, exactly like
    # pyinfra, so a netmask-less inet row is not an address.
    assert Ipv4Addrs.parse(
        [
            "en0: flags=8863<UP,BROADCAST,RUNNING> mtu 1500",
            "inet 1.2.3.4 broadcast 1.2.3.255",
        ]
    ) == {}


async def test_ipv4_addrs_reads_the_simulated_seam():
    addrs = {"eth0": ["192.168.0.240", "10.0.0.1"]}
    host = SimulatedHost(name="h", ipv4_addrs=addrs)
    assert await Ipv4Addrs.process(host) == addrs
    # The seam defaults to {}: a simulated machine with no interfaces.
    assert await Ipv4Addrs.process(SimulatedHost(name="h")) == {}


async def test_ipv4_addrs_seam_returns_a_copy():
    host = SimulatedHost(name="h", ipv4_addrs={"eth0": ["192.168.0.240"]})
    addrs = await Ipv4Addrs.process(host)
    addrs["eth0"].append("10.0.0.1")
    addrs["eth1"] = ["10.0.0.2"]
    assert host.ipv4_addrs == {"eth0": ["192.168.0.240"]}


async def test_ipv4_addrs_gather_fact_across_hosts():
    hosts = [
        SimulatedHost(name="web1", ipv4_addrs={"eth0": ["192.168.0.240"]}),
        SimulatedHost(name="web2"),
    ]
    assert await gather_fact(hosts, Ipv4Addrs) == {
        "web1": {"eth0": ["192.168.0.240"]},
        "web2": {},
    }


def test_ipv4_addrs_is_exported_through_the_namespace():
    # Both import paths resolve to the same class: hardware.Ipv4Addrs
    # through the package and Ipv4Addrs through the module source.
    assert hardware.Ipv4Addrs is Ipv4Addrs
    assert "Ipv4Addrs" in hardware.__all__


# --- Ipv6Addrs -----------------------------------------------------------------


async def test_ipv6_addrs_parses_pyintras_fixture():
    # pyinfra's own fact fixture
    # (tests/facts/hardware.Ipv6Addrs/linux_ip_multiple.json): text
    # `ip addr show` output. eth0 carries IPv4 aliases and one IPv6
    # address, eth1 a primary and an alias in listing order, and eth2 is
    # IPv4-only so it is omitted. The fixture's trailing padding on the
    # inet6 rows is stripped by parse.
    host = CannedShellHost(
        """2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc fq_codel state UP group default qlen 1000
link/ether c6:c5:60:cd:73:f1 brd ff:ff:ff:ff:ff:ff
inet 166.22.203.666/20 brd 165.22.207.255 scope global eth0
inet 10.18.0.10/16 brd 10.18.255.255 scope global eth0
inet6 fe80::c4c5:60ff:fecd:73f1/64 scope link
3: eth1: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc fq_codel state UP group default qlen 1000
link/ether c6:c5:60:cd:73:f1 brd ff:ff:ff:ff:ff:ff
inet6 fe80::c4c5:60ff:fecd:73f1/64 scope link
inet6 fe80::c4c5:60ff:fecd:abc/64 scope link
2: eth2: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc fq_codel state UP group default qlen 1000
link/ether c6:c5:60:cd:73:f1 brd ff:ff:ff:ff:ff:ff
inet 166.22.203.666/20 brd 165.22.207.255 scope global eth0
inet 10.18.0.10/16 brd 10.18.255.255 scope global eth0
"""
    )
    assert await Ipv6Addrs.process(host) == {
        "eth0": ["fe80::c4c5:60ff:fecd:73f1"],
        "eth1": ["fe80::c4c5:60ff:fecd:73f1", "fe80::c4c5:60ff:fecd:abc"],
    }
    assert host.shells == [
        "ip -j addr show 2> /dev/null || ip addr show 2> /dev/null || ifconfig -a"
    ]


def test_ipv6_addrs_parses_pyintras_documented_example():
    # pyinfra's docstring example result: eth0 -> ["fe80::a00:27ff::2"].
    # The doubled "::" is pyinfra's own value -- the row regex keeps the
    # raw string with no address validation.
    assert Ipv6Addrs.parse(
        [
            "1: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc fq_codel state UP group default qlen 1000",
            "inet6 fe80::a00:27ff::2/64 scope link",
        ]
    ) == {"eth0": ["fe80::a00:27ff::2"]}


async def test_ipv6_addrs_parses_pyintras_json_fixture():
    # pyinfra's NetworkDevices fixture
    # (tests/facts/hardware.NetworkDevices/linux_ip_json.json), verbatim:
    # `ip -j addr show` JSON with one inet and one inet6 entry -- only the
    # inet6 address survives the IPv6 projection.
    host = CannedShellHost(
        """[
  {
    "ifindex": 2,
    "ifname": "eth0",
    "flags": [
      "BROADCAST",
      "MULTICAST",
      "UP",
      "LOWER_UP"
    ],
    "mtu": 1500,
    "qdisc": "fq_codel",
    "operstate": "UP",
    "group": "default",
    "txqlen": 1000,
    "link_type": "ether",
    "address": "08:00:27:6d:95:c4",
    "broadcast": "ff:ff:ff:ff:ff:ff",
    "altnames": [
      "enx0800276d95c4"
    ],
    "addr_info": [
      {
        "family": "inet",
        "local": "192.168.1.2",
        "prefixlen": 24,
        "broadcast": "192.168.1.255",
        "scope": "global",
        "dynamic": true,
        "noprefixroute": true,
        "label": "eth0",
        "valid_life_time": 31856,
        "preferred_life_time": 31856
      },
      {
        "family": "inet6",
        "local": "2a01::01",
        "prefixlen": 64,
        "scope": "global",
        "dynamic": true,
        "noprefixroute": true,
        "valid_life_time": 28879,
        "preferred_life_time": 28879
      }
    ]
  }
]
"""
    )
    assert await Ipv6Addrs.process(host) == {"eth0": ["2a01::01"]}


def test_ipv6_addrs_json_keeps_aliases_in_listing_order():
    # Condensed from pyinfra's NetworkDevices fixture
    # (tests/facts/hardware.NetworkDevices/linux_ip_json_multiple_addrs.json):
    # two inet6 entries -- the primary first, then the alias pyinfra files
    # under additional_ips, in listing order; the inet entry is skipped.
    assert Ipv6Addrs.parse(
        [
            "[",
            "  {",
            '    "ifname": "eth0",',
            '    "addr_info": [',
            '      {"family": "inet", "local": "166.22.203.66", "prefixlen": 20},',
            '      {"family": "inet6", "local": "fe80::c4c5:60ff:fecd:73f1"},',
            '      {"family": "inet6", "local": "fe80::c4c5:60ff:fecd:abc"}',
            "    ]",
            "  }",
            "]",
        ]
    ) == {"eth0": ["fe80::c4c5:60ff:fecd:73f1", "fe80::c4c5:60ff:fecd:abc"]}


async def test_ipv6_addrs_parses_pyintras_ifconfig_fixture():
    # pyinfra's NetworkDevices fixture
    # (tests/facts/hardware.NetworkDevices/linux_ifconfig.json), verbatim:
    # the ifconfig fallback shape (prefixlen token, no CIDR slash). lo's
    # ::1 row carries no scope suffix and still matches.
    host = CannedShellHost(
        """enp1s0: flags=4163<UP,BROADCAST,RUNNING,MULTICAST>  mtu 1500
        inet 192.168.123.111  netmask 255.255.255.0  broadcast 192.168.123.255
        inet6 2a01:e0a:5c2:7450:b241:6fff:fe0a:cf22  prefixlen 64  scopeid 0x0<global>
        inet6 fe80::b241:6fff:fe0a:cf22  prefixlen 64  scopeid 0x20<link>
        ether b0:41:6f:0a:cf:22  txqueuelen 1000  (Ethernet)
        RX packets 52490  bytes 16827945 (16.0 MiB)
        RX errors 0  dropped 0  overruns 0  frame 0
        TX packets 21304  bytes 2948965 (2.8 MiB)
        TX errors 0  dropped 0 overruns 0  carrier 0  collisions 0
incusbr0: flags=4163<UP,BROADCAST,RUNNING,MULTICAST>  mtu 1500
        inet 10.232.181.1  netmask 255.255.255.0  broadcast 0.0.0.0
        inet6 fe80::216:3eff:fe9c:8200  prefixlen 64  scopeid 0x20<link>
        inet6 fd42:5bce:bd73:eda6::1  prefixlen 64  scopeid 0x0<global>
        ether 00:16:3e:9c:82:00  txqueuelen 1000  (Ethernet)
        RX packets 2592  bytes 253292 (247.3 KiB)
        RX errors 0  dropped 0  overruns 0  frame 0
        TX packets 1289  bytes 185014 (180.6 KiB)
        TX errors 0  dropped 0 overruns 0  carrier 0  collisions 0
lo: flags=73<UP,LOOPBACK,RUNNING>  mtu 65536
        inet 127.0.0.1  netmask 255.0.0.0
        inet6 ::1  prefixlen 128  scopeid 0x10<host>
        loop  txqueuelen 1000  (Local Loopback)
        RX packets 34  bytes 2816 (2.7 KiB)
        RX errors 0  dropped 0  overruns 0  frame 0
        TX packets 34  bytes 2816 (2.7 KiB)
        TX errors 0  dropped 0 overruns 0  carrier 0  collisions 0
"""
    )
    assert await Ipv6Addrs.process(host) == {
        "enp1s0": [
            "2a01:e0a:5c2:7450:b241:6fff:fe0a:cf22",
            "fe80::b241:6fff:fe0a:cf22",
        ],
        "incusbr0": ["fe80::216:3eff:fe9c:8200", "fd42:5bce:bd73:eda6::1"],
        "lo": ["::1"],
    }


def test_ipv6_addrs_parses_pyintras_macos_ifconfig_fixture():
    # pyinfra's NetworkDevices fixture
    # (tests/facts/hardware.NetworkDevices/macos_ifconfig.json), verbatim:
    # the macOS ifconfig shape -- the inet6 row has a prefixlen token and
    # no CIDR slash, so only the second row regex matches.
    assert Ipv6Addrs.parse(
        [
            "en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500",
            "inet 1.2.3.4 netmask 0xffffff00 broadcast 255.255.255.255",
            "inet6 2a01::01 prefixlen 64 autoconf temporary",
            "nd6 options=201<PERFORMNUD,DAD>",
            "media: autoselect",
            "status: active",
        ]
    ) == {"en0": ["2a01::01"]}


async def test_ipv6_addrs_is_empty_on_empty_or_malformed_output():
    # pyinfra's NetworkDevices empty/noise fixtures: empty output, an
    # empty JSON list, a JSON list holding one empty dict (no ifname), and
    # line noise that fails the JSON sniff and matches no device section.
    assert await Ipv6Addrs.process(CannedShellHost("")) == {}
    assert Ipv6Addrs.parse([]) == {}
    assert Ipv6Addrs.parse(["[]"]) == {}
    assert Ipv6Addrs.parse(["[{", "}]"]) == {}
    assert Ipv6Addrs.parse(["[%^&$*&^%(*&%^&%#$"]) == {}


def test_ipv6_addrs_json_skips_unusable_entries():
    # A device without an ifname is dropped (pyinfra's missing_ifname
    # fixture); addr entries without a local or a family are skipped, and
    # inet entries never enter the IPv6 projection.
    assert Ipv6Addrs.parse(
        ['[{"addr_info": [{"family": "inet6", "local": "fe80::1"}]}]']
    ) == {}
    assert Ipv6Addrs.parse(
        [
            '[{"ifname": "eth0", "addr_info": [{"family": "inet6"}, {"local": "fe80::2"}, {"family": "inet", "local": "10.0.0.1"}, {"family": "inet6", "local": "fe80::1"}]}]'
        ]
    ) == {"eth0": ["fe80::1"]}
    # Non-dict devices or addr entries are skipped rather than raising
    # (pyinfra's walker would crash; reemote5 hardens the parser). The
    # first element stays a dict so the JSON sniff still fires.
    assert Ipv6Addrs.parse(
        ['[{"ifname": "eth0", "addr_info": [5, {"family": "inet6", "local": "fe80::1"}]}, 7]']
    ) == {"eth0": ["fe80::1"]}


def test_ipv6_addrs_json_drops_an_empty_primary_address():
    # pyinfra's projection skips an interface whose primary address is
    # empty -- even when a usable alias follows it.
    assert Ipv6Addrs.parse(
        '[{"ifname": "eth0", "addr_info": ['
        '{"family": "inet6", "local": ""}, '
        '{"family": "inet6", "local": "fe80::1"}]}]'
    ) == {}


def test_ipv6_addrs_text_keeps_the_last_duplicate_section():
    # A later section for the same interface wins, like pyinfra's device
    # map -- including dropping the interface when the last section
    # carries no IPv6 address.
    assert Ipv6Addrs.parse(
        [
            "2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 state UP",
            "inet6 fe80::1/64 scope link",
            "3: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 state UP",
            "inet6 fe80::2/64 scope link",
        ]
    ) == {"eth0": ["fe80::2"]}
    assert Ipv6Addrs.parse(
        [
            "2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 state UP",
            "inet6 fe80::1/64 scope link",
            "3: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 state UP",
            "inet 10.0.0.1/24 brd 10.0.0.255 scope global eth0",
        ]
    ) == {}


def test_ipv6_addrs_ifconfig_rows_without_a_prefixlen_never_match():
    # The ip-a regex requires the CIDR slash and the ifconfig regex the
    # prefixlen token, exactly like pyinfra, so an inet6 row carrying
    # neither is not an address.
    assert Ipv6Addrs.parse(
        [
            "en0: flags=8863<UP,BROADCAST,RUNNING> mtu 1500",
            "inet6 fe80::1 scope link",
        ]
    ) == {}


async def test_ipv6_addrs_reads_the_simulated_seam():
    addrs = {"eth0": ["fe80::c4c5:60ff:fecd:73f1", "2a01::01"]}
    host = SimulatedHost(name="h", ipv6_addrs=addrs)
    assert await Ipv6Addrs.process(host) == addrs
    # The seam defaults to {}: a simulated machine with no interfaces.
    assert await Ipv6Addrs.process(SimulatedHost(name="h")) == {}


async def test_ipv6_addrs_seam_returns_a_copy():
    host = SimulatedHost(name="h", ipv6_addrs={"eth0": ["fe80::1"]})
    addrs = await Ipv6Addrs.process(host)
    addrs["eth0"].append("2a01::01")
    addrs["eth1"] = ["fe80::2"]
    assert host.ipv6_addrs == {"eth0": ["fe80::1"]}


async def test_ipv6_addrs_gather_fact_across_hosts():
    hosts = [
        SimulatedHost(name="web1", ipv6_addrs={"eth0": ["fe80::1"]}),
        SimulatedHost(name="web2"),
    ]
    assert await gather_fact(hosts, Ipv6Addrs) == {
        "web1": {"eth0": ["fe80::1"]},
        "web2": {},
    }


def test_ipv6_addrs_is_exported_through_the_namespace():
    # Both import paths resolve to the same class: hardware.Ipv6Addrs
    # through the package and Ipv6Addrs through the module source.
    assert hardware.Ipv6Addrs is Ipv6Addrs
    assert "Ipv6Addrs" in hardware.__all__


# --- LsBlk ---------------------------------------------------------------------


async def test_lsblk_parses_the_full_device_tree():
    # Realistic `lsblk -J -b -o NAME,TYPE,SIZE,MOUNTPOINT` output: a
    # partitioned disk with an LVM volume stacked on sda2, plus a whole
    # unmounted disk (sdb) that the df-based BlockDevices never sees.
    host = CannedShellHost(
        """{
   "blockdevices": [
      {
         "name": "sda",
         "type": "disk",
         "size": 21474836480,
         "mountpoint": null,
         "children": [
            {
               "name": "sda1",
               "type": "part",
               "size": 1073741824,
               "mountpoint": "/boot"
            },
            {
               "name": "sda2",
               "type": "part",
               "size": 20400037888,
               "mountpoint": null,
               "children": [
                  {
                     "name": "vg0-root",
                     "type": "lvm",
                     "size": 16106127360,
                     "mountpoint": "/"
                  }
               ]
            }
         ]
      },
      {
         "name": "sdb",
         "type": "disk",
         "size": 10737418240,
         "mountpoint": null
      }
   ]
}
"""
    )
    assert await LsBlk.process(host) == [
        {
            "name": "sda",
            "type": "disk",
            "size": 21474836480,
            "mountpoint": None,
            "children": [
                {
                    "name": "sda1",
                    "type": "part",
                    "size": 1073741824,
                    "mountpoint": "/boot",
                },
                {
                    "name": "sda2",
                    "type": "part",
                    "size": 20400037888,
                    "mountpoint": None,
                    "children": [
                        {
                            "name": "vg0-root",
                            "type": "lvm",
                            "size": 16106127360,
                            "mountpoint": "/",
                        }
                    ],
                },
            ],
        },
        {
            "name": "sdb",
            "type": "disk",
            "size": 10737418240,
            "mountpoint": None,
        },
    ]
    assert host.shells == ["lsblk -J -b -o NAME,TYPE,SIZE,MOUNTPOINT"]


def test_lsblk_parse_keeps_sizes_as_ints_and_unmounted_as_none():
    # The -b byte counts stay JSON ints and an unmounted device keeps its
    # null mountpoint -- no string coercion, no filtering.
    assert LsBlk.parse(
        ['{"blockdevices": [{"name": "sdb", "type": "disk", "size": 512, "mountpoint": null}]}']
    ) == [{"name": "sdb", "type": "disk", "size": 512, "mountpoint": None}]


def test_lsblk_parse_skips_non_dict_nodes_at_every_level():
    # Non-dict entries are skipped in blockdevices and in children, and a
    # non-list children value is dropped rather than raising.
    assert LsBlk.parse(
        [
            """{"blockdevices": [7, {"name": "sda", "children": [5, {"name": "sda1"}]}, {"name": "sdb", "children": "nope"}]}"""
        ]
    ) == [
        {"name": "sda", "children": [{"name": "sda1"}]},
        {"name": "sdb"},
    ]


def test_lsblk_parse_reads_unusable_documents_as_empty():
    # Malformed JSON, a non-dict document, a missing blockdevices key and
    # a non-list blockdevices value all read as [].
    assert LsBlk.parse(["{", "}"]) == []
    assert LsBlk.parse(["[]"]) == []
    assert LsBlk.parse(['{"other": []}']) == []
    assert LsBlk.parse(['{"blockdevices": "nope"}']) == []
    assert LsBlk.parse(['{"blockdevices": []}']) == []


async def test_lsblk_is_empty_on_empty_output():
    # A failed read maps to [], the house empty-fallback for list facts.
    assert await LsBlk.process(CannedShellHost("")) == []
    assert LsBlk.parse([]) == []


async def test_lsblk_reads_the_simulated_seam():
    devices = [
        {
            "name": "sda",
            "type": "disk",
            "size": 21474836480,
            "mountpoint": None,
            "children": [
                {
                    "name": "sda1",
                    "type": "part",
                    "size": 1073741824,
                    "mountpoint": "/boot",
                }
            ],
        }
    ]
    host = SimulatedHost(name="h", lsblk_devices=devices)
    assert await LsBlk.process(host) == devices
    # The seam defaults to []: a simulated machine with no devices listed.
    assert await LsBlk.process(SimulatedHost(name="h")) == []


async def test_lsblk_seam_returns_a_deep_copy():
    host = SimulatedHost(
        name="h",
        lsblk_devices=[
            {
                "name": "sda",
                "type": "disk",
                "children": [{"name": "sda1", "mountpoint": "/boot"}],
            }
        ],
    )
    devices = await LsBlk.process(host)
    devices[0]["type"] = "lvm"
    devices[0]["children"][0]["mountpoint"] = "/data"
    devices.append({"name": "sdb"})
    assert host.lsblk_devices == [
        {
            "name": "sda",
            "type": "disk",
            "children": [{"name": "sda1", "mountpoint": "/boot"}],
        }
    ]


async def test_lsblk_gather_fact_across_hosts():
    hosts = [
        SimulatedHost(name="web1", lsblk_devices=[{"name": "sda", "type": "disk"}]),
        SimulatedHost(name="web2"),
    ]
    assert await gather_fact(hosts, LsBlk) == {
        "web1": [{"name": "sda", "type": "disk"}],
        "web2": [],
    }


def test_lsblk_is_exported_through_the_namespace():
    # Both import paths resolve to the same class: hardware.LsBlk through
    # the package and LsBlk through the module source.
    assert hardware.LsBlk is LsBlk
    assert "LsBlk" in hardware.__all__


# --- NetworkDevices ------------------------------------------------------------


async def test_network_devices_parses_pyintras_ip_fixture():
    # pyinfra's own fact fixture
    # (tests/facts/hardware.NetworkDevices/linux_ip.json), verbatim: the
    # plain-text `ip addr show` fallback with one device, both families
    # and the ether/mtu/state details.
    host = CannedShellHost(
        """2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc fq_codel state UP group default qlen 1000
link/ether 08:00:27:6d:95:c4 brd ff:ff:ff:ff:ff:ff
inet 1.2.3.4/24 brd 1.2.3.4 scope global dynamic eth0
valid_lft 86343sec preferred_lft 86343sec
inet6 2a01::01/64 scope link
valid_lft forever preferred_lft forever
"""
    )
    assert await NetworkDevices.process(host) == {
        "eth0": {
            "ether": "08:00:27:6d:95:c4",
            "mtu": 1500,
            "state": "UP",
            "ipv4": {
                "address": "1.2.3.4",
                "mask_bits": 24,
                "netmask": "255.255.255.0",
                "broadcast": "1.2.3.4",
            },
            "ipv6": {"address": "2a01::01", "mask_bits": 64},
        }
    }
    assert host.shells == [
        "ip -j addr show 2> /dev/null || ip addr show 2> /dev/null || ifconfig -a"
    ]


async def test_network_devices_parses_pyintras_json_fixture():
    # pyinfra's own fact fixture
    # (tests/facts/hardware.NetworkDevices/linux_ip_json.json), verbatim:
    # the `ip -j addr show` JSON branch -- prefixlen becomes mask_bits and
    # the IPv4 netmask is expanded from it.
    host = CannedShellHost(
        """[
  {
    "ifindex": 2,
    "ifname": "eth0",
    "flags": [
      "BROADCAST",
      "MULTICAST",
      "UP",
      "LOWER_UP"
    ],
    "mtu": 1500,
    "qdisc": "fq_codel",
    "operstate": "UP",
    "group": "default",
    "txqlen": 1000,
    "link_type": "ether",
    "address": "08:00:27:6d:95:c4",
    "broadcast": "ff:ff:ff:ff:ff:ff",
    "altnames": [
      "enx0800276d95c4"
    ],
    "addr_info": [
      {
        "family": "inet",
        "local": "192.168.1.2",
        "prefixlen": 24,
        "broadcast": "192.168.1.255",
        "scope": "global",
        "dynamic": true,
        "noprefixroute": true,
        "label": "eth0",
        "valid_life_time": 31856,
        "preferred_life_time": 31856
      },
      {
        "family": "inet6",
        "local": "2a01::01",
        "prefixlen": 64,
        "scope": "global",
        "dynamic": true,
        "noprefixroute": true,
        "valid_life_time": 28879,
        "preferred_life_time": 28879
      }
    ]
  }
]
"""
    )
    assert await NetworkDevices.process(host) == {
        "eth0": {
            "ether": "08:00:27:6d:95:c4",
            "mtu": 1500,
            "state": "UP",
            "ipv4": {
                "address": "192.168.1.2",
                "mask_bits": 24,
                "netmask": "255.255.255.0",
                "broadcast": "192.168.1.255",
            },
            "ipv6": {"address": "2a01::01", "mask_bits": 64},
        }
    }


async def test_network_devices_parses_pyintras_ifconfig_fixture():
    # pyinfra's own fact fixture
    # (tests/facts/hardware.NetworkDevices/linux_ifconfig.json), verbatim:
    # the ifconfig fallback -- dotted-quad netmasks count their bits, the
    # second inet6 row per device lands under additional_ips, lo's missing
    # broadcast reads as None and its loop line carries no ether.
    host = CannedShellHost(
        """enp1s0: flags=4163<UP,BROADCAST,RUNNING,MULTICAST>  mtu 1500
        inet 192.168.123.111  netmask 255.255.255.0  broadcast 192.168.123.255
        inet6 2a01:e0a:5c2:7450:b241:6fff:fe0a:cf22  prefixlen 64  scopeid 0x0<global>
        inet6 fe80::b241:6fff:fe0a:cf22  prefixlen 64  scopeid 0x20<link>
        ether b0:41:6f:0a:cf:22  txqueuelen 1000  (Ethernet)
        RX packets 52490  bytes 16827945 (16.0 MiB)
        RX errors 0  dropped 0  overruns 0  frame 0
        TX packets 21304  bytes 2948965 (2.8 MiB)
        TX errors 0  dropped 0 overruns 0  carrier 0  collisions 0
incusbr0: flags=4163<UP,BROADCAST,RUNNING,MULTICAST>  mtu 1500
        inet 10.232.181.1  netmask 255.255.255.0  broadcast 0.0.0.0
        inet6 fe80::216:3eff:fe9c:8200  prefixlen 64  scopeid 0x20<link>
        inet6 fd42:5bce:bd73:eda6::1  prefixlen 64  scopeid 0x0<global>
        ether 00:16:3e:9c:82:00  txqueuelen 1000  (Ethernet)
        RX packets 2592  bytes 253292 (247.3 KiB)
        RX errors 0  dropped 0  overruns 0  frame 0
        TX packets 1289  bytes 185014 (180.6 KiB)
        TX errors 0  dropped 0 overruns 0  carrier 0  collisions 0
lo: flags=73<UP,LOOPBACK,RUNNING>  mtu 65536
        inet 127.0.0.1  netmask 255.0.0.0
        inet6 ::1  prefixlen 128  scopeid 0x10<host>
        loop  txqueuelen 1000  (Local Loopback)
        RX packets 34  bytes 2816 (2.7 KiB)
        RX errors 0  dropped 0  overruns 0  frame 0
        TX packets 34  bytes 2816 (2.7 KiB)
        TX errors 0  dropped 0 overruns 0  carrier 0  collisions 0
"""
    )
    assert await NetworkDevices.process(host) == {
        "enp1s0": {
            "ether": "b0:41:6f:0a:cf:22",
            "mtu": 1500,
            "state": "UP",
            "ipv4": {
                "address": "192.168.123.111",
                "mask_bits": 24,
                "netmask": "255.255.255.0",
                "broadcast": "192.168.123.255",
            },
            "ipv6": {
                "address": "2a01:e0a:5c2:7450:b241:6fff:fe0a:cf22",
                "mask_bits": 64,
                "additional_ips": [
                    {"address": "fe80::b241:6fff:fe0a:cf22", "mask_bits": 64}
                ],
            },
        },
        "incusbr0": {
            "ether": "00:16:3e:9c:82:00",
            "mtu": 1500,
            "state": "UP",
            "ipv4": {
                "address": "10.232.181.1",
                "mask_bits": 24,
                "netmask": "255.255.255.0",
                "broadcast": "0.0.0.0",
            },
            "ipv6": {
                "address": "fe80::216:3eff:fe9c:8200",
                "mask_bits": 64,
                "additional_ips": [
                    {"address": "fd42:5bce:bd73:eda6::1", "mask_bits": 64}
                ],
            },
        },
        "lo": {
            "mtu": 65536,
            "state": "UP",
            "ipv4": {
                "address": "127.0.0.1",
                "mask_bits": 8,
                "netmask": "255.0.0.0",
                "broadcast": None,
            },
            "ipv6": {"address": "::1", "mask_bits": 128},
        },
    }


def test_network_devices_parses_pyintras_macos_ifconfig_fixture():
    # pyinfra's own fact fixture
    # (tests/facts/hardware.NetworkDevices/macos_ifconfig.json), verbatim:
    # the macOS shape -- the hex netmask 0xffffff00 counts 24 bits and
    # stays normalized to its dotted quad, and en0 lists no ether row.
    assert NetworkDevices.parse(
        [
            "en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500",
            "inet 1.2.3.4 netmask 0xffffff00 broadcast 255.255.255.255",
            "inet6 2a01::01 prefixlen 64 autoconf temporary",
            "nd6 options=201<PERFORMNUD,DAD>",
            "media: autoselect",
            "status: active",
        ]
    ) == {
        "en0": {
            "mtu": 1500,
            "state": "UP",
            "ipv4": {
                "address": "1.2.3.4",
                "mask_bits": 24,
                "netmask": "255.255.255.0",
                "broadcast": "255.255.255.255",
            },
            "ipv6": {"address": "2a01::01", "mask_bits": 64},
        }
    }


def test_network_devices_json_keeps_additional_ips():
    # Condensed from pyinfra's fixture
    # (tests/facts/hardware.NetworkDevices/linux_ip_json_multiple_addrs.json):
    # the first inet entry is the primary ipv4 block, the second collects
    # under additional_ips with its own mask/broadcast details.
    assert NetworkDevices.parse(
        [
            "[",
            "  {",
            '    "ifname": "eth0",',
            '    "operstate": "UP",',
            '    "mtu": 1500,',
            '    "address": "c6:c5:60:cd:73:f1",',
            '    "addr_info": [',
            '      {"family": "inet", "local": "166.22.203.66", "prefixlen": 20, "broadcast": "165.22.207.255"},',
            '      {"family": "inet", "local": "10.18.0.10", "prefixlen": 16, "broadcast": "10.18.255.255"},',
            '      {"family": "inet6", "local": "fe80::c4c5:60ff:fecd:73f1", "prefixlen": 64}',
            "    ]",
            "  }",
            "]",
        ]
    ) == {
        "eth0": {
            "ether": "c6:c5:60:cd:73:f1",
            "mtu": 1500,
            "state": "UP",
            "ipv4": {
                "address": "166.22.203.66",
                "mask_bits": 20,
                "netmask": "255.255.240.0",
                "broadcast": "165.22.207.255",
                "additional_ips": [
                    {
                        "address": "10.18.0.10",
                        "mask_bits": 16,
                        "netmask": "255.255.0.0",
                        "broadcast": "10.18.255.255",
                    }
                ],
            },
            "ipv6": {"address": "fe80::c4c5:60ff:fecd:73f1", "mask_bits": 64},
        }
    }


def test_network_devices_json_without_prefixlen_omits_mask_keys():
    # Condensed from pyinfra's fixture
    # (tests/facts/hardware.NetworkDevices/linux_ip_json_no_prefixlen_ok.json):
    # without a prefixlen neither mask_bits nor netmask appears, but the
    # IPv4 block still carries its (possibly None) broadcast.
    assert NetworkDevices.parse(
        [
            "[",
            "  {",
            '    "ifname": "eth0",',
            '    "operstate": "UP",',
            '    "addr_info": [',
            '      {"family": "inet", "local": "166.22.203.66", "broadcast": "165.22.207.255"},',
            '      {"family": "inet6", "local": "fe80::c4c5:60ff:fecd:73f1"}',
            "    ]",
            "  }",
            "]",
        ]
    ) == {
        "eth0": {
            "state": "UP",
            "ipv4": {"address": "166.22.203.66", "broadcast": "165.22.207.255"},
            "ipv6": {"address": "fe80::c4c5:60ff:fecd:73f1"},
        }
    }


def test_network_devices_text_ipv4_aliases_stay_raw():
    # pyinfra's own fact fixture
    # (tests/facts/hardware.NetworkDevices/linux_ip_multiple_addrs.json),
    # verbatim: the invalid octet 166.22.203.666 stays a raw string (no
    # validation), and the second inet row becomes an additional_ips alias.
    assert NetworkDevices.parse(
        [
            "2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc fq_codel state UP group default qlen 1000",
            "link/ether c6:c5:60:cd:73:f1 brd ff:ff:ff:ff:ff:ff",
            "inet 166.22.203.666/20 brd 165.22.207.255 scope global eth0",
            "valid_lft forever preferred_lft forever",
            "inet 10.18.0.10/16 brd 10.18.255.255 scope global eth0",
            "valid_lft forever preferred_lft forever",
            "inet6 fe80::c4c5:60ff:fecd:73f1/64 scope link ",
            "valid_lft forever preferred_lft forever",
        ]
    ) == {
        "eth0": {
            "ether": "c6:c5:60:cd:73:f1",
            "mtu": 1500,
            "state": "UP",
            "ipv4": {
                "address": "166.22.203.666",
                "mask_bits": 20,
                "netmask": "255.255.240.0",
                "broadcast": "165.22.207.255",
                "additional_ips": [
                    {
                        "address": "10.18.0.10",
                        "mask_bits": 16,
                        "netmask": "255.255.0.0",
                        "broadcast": "10.18.255.255",
                    }
                ],
            },
            "ipv6": {"address": "fe80::c4c5:60ff:fecd:73f1", "mask_bits": 64},
        }
    }


def test_network_devices_text_state_heuristic_and_at_names():
    # Condensed from pyinfra's fixture
    # (tests/facts/hardware.NetworkDevices/linux_ip_multiple_ifaces.json):
    # the state is a token heuristic over the whole section (DOWN without
    # UP reads DOWN, neither reads UNKNOWN), and a vlan's device name keeps
    # its @parent suffix in the text branch.
    assert NetworkDevices.parse(
        [
            "3: wlp6s0: <BROADCAST,MULTICAST> mtu 1500 qdisc noop state DOWN group default qlen 1000",
            "link/ether b8:4c:68:59:45:7d brd ff:ff:ff:ff:ff:ff permaddr 64:5d:24:b6:ad:5c",
            "4: myvlan@eno1: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc noqueue state UP group default qlen 1000",
            "link/ether 3e:d2:56:4d:1a:f2 brd ff:ff:ff:ff:ff:ff",
            "inet 10.42.13.147/24 brd 10.42.13.255 scope global dynamic noprefixroute myvlan",
            "veth-end: flags=4098<BROADCAST,MULTICAST>  mtu 1500",
            "ether 96:1d:5c:3b:20:9e  txqueuelen 1000  (Ethernet)",
        ]
    ) == {
        "wlp6s0": {"ether": "b8:4c:68:59:45:7d", "mtu": 1500, "state": "DOWN"},
        "myvlan@eno1": {
            "ether": "3e:d2:56:4d:1a:f2",
            "mtu": 1500,
            "state": "UP",
            "ipv4": {
                "address": "10.42.13.147",
                "mask_bits": 24,
                "netmask": "255.255.255.0",
                "broadcast": "10.42.13.255",
            },
        },
        "veth-end": {"ether": "96:1d:5c:3b:20:9e", "mtu": 1500, "state": "UNKNOWN"},
    }


async def test_network_devices_is_empty_on_empty_or_malformed_output():
    # pyinfra's empty/noise fixtures: empty output, an empty JSON list, a
    # JSON list holding one empty dict (no ifname), and line noise that
    # fails the JSON sniff and matches no device section.
    assert await NetworkDevices.process(CannedShellHost("")) == {}
    assert NetworkDevices.parse([]) == {}
    assert NetworkDevices.parse(["[]"]) == {}
    assert NetworkDevices.parse(["[{", "}"]) == {}
    assert NetworkDevices.parse(["[%^&$*&^%(*&%^&%#$"]) == {}


def test_network_devices_json_skips_unusable_entries():
    # A device without an ifname is dropped (pyinfra's missing_ifname
    # fixture); addr entries without a local or a family are skipped but
    # the device keeps its state; a missing operstate reads UNKNOWN.
    assert NetworkDevices.parse(
        ['[{"addr_info": [{"family": "inet6", "local": "fe80::1"}]}]']
    ) == {}
    assert NetworkDevices.parse(
        [
            '[{"ifname": "eth0", "addr_info": [{"family": "inet6"}, {"local": "fe80::2"}, {"family": "link", "local": "aa:bb"}, {"family": "inet6", "local": "fe80::1"}]}]'
        ]
    ) == {"eth0": {"state": "UNKNOWN", "ipv6": {"address": "fe80::1"}}}
    # Non-dict devices or addr entries are skipped rather than raising
    # (pyinfra's walker would crash; reemote5 hardens the parser). The
    # first element stays a dict so the JSON sniff still fires.
    assert NetworkDevices.parse(
        ['[{"ifname": "eth0", "addr_info": [5, {"family": "inet6", "local": "fe80::1"}]}, 7]']
    ) == {"eth0": {"state": "UNKNOWN", "ipv6": {"address": "fe80::1"}}}


def test_network_devices_text_keeps_the_last_duplicate_section():
    # A later section for the same interface wins outright, like pyinfra's
    # device map -- including losing the earlier section's addresses.
    assert NetworkDevices.parse(
        [
            "2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 state UP",
            "inet 10.0.0.1/24 brd 10.0.0.255 scope global eth0",
            "3: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 9000 state UP",
            "inet6 fe80::2/64 scope link",
        ]
    ) == {
        "eth0": {"mtu": 9000, "state": "UP", "ipv6": {"address": "fe80::2", "mask_bits": 64}}
    }


def test_network_devices_ifconfig_rows_without_a_netmask_never_match():
    # The ip-a regex requires the CIDR slash and the ifconfig regex the
    # netmask/prefixlen token, exactly like pyinfra, so token-less rows
    # contribute no address block -- the device still lists.
    assert NetworkDevices.parse(
        [
            "en0: flags=8863<UP,BROADCAST,RUNNING> mtu 1500",
            "inet 10.0.0.1 broadcast 10.0.0.255",
            "inet6 fe80::1 scope link",
        ]
    ) == {"en0": {"mtu": 1500, "state": "UP"}}


async def test_network_devices_reads_the_simulated_seam():
    # pyinfra's documented example result (the enp1s0/wlo1 entries): the
    # seam holds the parsed device dict verbatim, additional_ips included.
    devices = {
        "enp1s0": {
            "ether": "12:34:56:78:9A:BC",
            "mtu": 1500,
            "state": "UP",
            "ipv4": {
                "address": "192.168.1.100",
                "mask_bits": 24,
                "netmask": "255.255.255.0",
            },
            "ipv6": {
                "address": "2001:db8:85a3::8a2e:370:7334",
                "mask_bits": 64,
                "additional_ips": [
                    {"address": "fe80::1234:5678:9abc:def0", "mask_bits": 64}
                ],
            },
        },
        "wlo1": {"ether": "77:88:99:AA:BB:CC", "mtu": 1500, "state": "UNKNOWN"},
    }
    host = SimulatedHost(name="h", network_devices=devices)
    assert await NetworkDevices.process(host) == devices
    # The seam defaults to {}: a simulated machine with no interfaces.
    assert await NetworkDevices.process(SimulatedHost(name="h")) == {}


async def test_network_devices_seam_returns_a_copy():
    host = SimulatedHost(
        name="h",
        network_devices={
            "eth0": {
                "state": "UP",
                "ipv4": {
                    "address": "10.0.0.1",
                    "additional_ips": [{"address": "10.0.0.2"}],
                },
            }
        },
    )
    devices = await NetworkDevices.process(host)
    devices["eth0"]["state"] = "DOWN"
    devices["eth0"]["ipv4"]["address"] = "10.9.9.9"
    devices["eth0"]["ipv4"]["additional_ips"][0]["address"] = "10.9.9.8"
    devices["eth1"] = {"state": "UP"}
    assert host.network_devices == {
        "eth0": {
            "state": "UP",
            "ipv4": {
                "address": "10.0.0.1",
                "additional_ips": [{"address": "10.0.0.2"}],
            },
        }
    }


async def test_network_devices_gather_fact_across_hosts():
    hosts = [
        SimulatedHost(name="web1", network_devices={"eth0": {"state": "UP"}}),
        SimulatedHost(name="web2"),
    ]
    assert await gather_fact(hosts, NetworkDevices) == {
        "web1": {"eth0": {"state": "UP"}},
        "web2": {},
    }


def test_network_devices_is_exported_through_the_namespace():
    # Both import paths resolve to the same class: hardware.NetworkDevices
    # through the package and NetworkDevices through the module source.
    assert hardware.NetworkDevices is NetworkDevices
    assert "NetworkDevices" in hardware.__all__


# --- SmbiosInfo ----------------------------------------------------------------


async def test_smbios_info_parses_a_dmidecode_dump():
    # A realistic `dmidecode -q` dump on a Dell server: sections keyed by
    # their verbatim titles, the Characteristics items collected into a
    # list, and the chassis Type already decoded to its label.
    host = CannedShellHost(
        """BIOS Information
\tVendor: Dell Inc.
\tVersion: 2.12.2
\tRelease Date: 07/09/2021
\tCharacteristics:
\t\tPCI is supported
\t\tBIOS is upgradeable
\tBIOS Revision: 2.12

System Information
\tManufacturer: Dell Inc.
\tProduct Name: PowerEdge R640
\tSerial Number: 7XYZ123
\tUUID: 4c4c4544-0058-5910-805a-b7c04f313233
\tWake-up Type: Power Switch

Chassis Information
\tManufacturer: Dell Inc.
\tType: Rack Mount Chassis
\tLock: Present
"""
    )
    assert await SmbiosInfo.process(host) == {
        "BIOS Information": {
            "Vendor": "Dell Inc.",
            "Version": "2.12.2",
            "Release Date": "07/09/2021",
            "Characteristics": ["PCI is supported", "BIOS is upgradeable"],
            "BIOS Revision": "2.12",
        },
        "System Information": {
            "Manufacturer": "Dell Inc.",
            "Product Name": "PowerEdge R640",
            "Serial Number": "7XYZ123",
            "UUID": "4c4c4544-0058-5910-805a-b7c04f313233",
            "Wake-up Type": "Power Switch",
        },
        "Chassis Information": {
            "Manufacturer": "Dell Inc.",
            "Type": "Rack Mount Chassis",
            "Lock": "Present",
        },
    }
    assert host.shells == [
        "dmidecode -q 2> /dev/null || sudo -n dmidecode -q 2> /dev/null || true"
    ]


def test_smbios_info_parse_empty_valued_field_stays_a_string_without_items():
    # A colon-bearing field with no value and no deeper-indented items
    # reads as ""; the first item converts it to a list, and a value with
    # an inner colon keeps everything after the first colon.
    assert SmbiosInfo.parse(
        [
            "OEM Strings",
            "\tString 1:",
            "\tString 2: Dell System",
            "\tString 3: 5[0003:0002]",
        ]
    ) == {
        "OEM Strings": {
            "String 1": "",
            "String 2": "Dell System",
            "String 3": "5[0003:0002]",
        }
    }


def test_smbios_info_parse_keeps_the_last_duplicate_section():
    # Memory Device appears once per DIMM: a repeated title keeps the last
    # occurrence, the house last-wins rule (the identity/BIOS/chassis
    # sections each appear once).
    assert SmbiosInfo.parse(
        [
            "Memory Device",
            "\tSize: 16 GB",
            "\tLocator: DIMM A1",
            "Memory Device",
            "\tSize: 32 GB",
            "\tLocator: DIMM B1",
        ]
    ) == {"Memory Device": {"Size": "32 GB", "Locator": "DIMM B1"}}


def test_smbios_info_parse_skips_blank_colonless_and_orphan_rows():
    # Blank lines, indented rows before any section, a colon-less field
    # row and an item with no preceding field all contribute nothing.
    assert SmbiosInfo.parse(
        [
            "",
            "\tOrphan Field: before any section",
            "System Boot Information",
            "\t\torphan item",
            "\tNot a key value row",
            "\tStatus: No errors detected",
            "   ",
        ]
    ) == {"System Boot Information": {"Status": "No errors detected"}}


async def test_smbios_info_is_empty_on_empty_output():
    # A failed read (missing dmidecode, no root or passwordless sudo, no
    # SMBIOS tables) is swallowed by the command -- including the sudo -n
    # retry, which never prompts -- and maps to {}, the house dict-fact
    # fallback.
    assert await SmbiosInfo.process(CannedShellHost("")) == {}


async def test_smbios_info_reads_the_simulated_seam():
    info = {
        "System Information": {"Manufacturer": "VMware, Inc."},
        "Chassis Information": {"Type": "Other"},
    }
    host = SimulatedHost(name="h", smbios_info=info)
    assert await SmbiosInfo.process(host) == info
    # The seam defaults to {}: a simulated machine whose dmidecode read
    # fails.
    assert await SmbiosInfo.process(SimulatedHost(name="h")) == {}


async def test_smbios_info_seam_returns_a_copy():
    host = SimulatedHost(
        name="h",
        smbios_info={
            "BIOS Information": {
                "Vendor": "Dell Inc.",
                "Characteristics": ["PCI is supported"],
            }
        },
    )
    info = await SmbiosInfo.process(host)
    info["BIOS Information"]["Vendor"] = "HPE"
    info["BIOS Information"]["Characteristics"].append("BIOS is upgradeable")
    info["System Information"] = {"Manufacturer": "HPE"}
    assert host.smbios_info == {
        "BIOS Information": {
            "Vendor": "Dell Inc.",
            "Characteristics": ["PCI is supported"],
        }
    }


async def test_smbios_info_gather_fact_across_hosts():
    hosts = [
        SimulatedHost(
            name="web1", smbios_info={"Chassis Information": {"Type": "Rack Mount Chassis"}}
        ),
        SimulatedHost(name="web2"),
    ]
    assert await gather_fact(hosts, SmbiosInfo) == {
        "web1": {"Chassis Information": {"Type": "Rack Mount Chassis"}},
        "web2": {},
    }


def test_smbios_info_is_exported_through_the_namespace():
    # Both import paths resolve to the same class: hardware.SmbiosInfo
    # through the package and SmbiosInfo through the module source.
    assert hardware.SmbiosInfo is SmbiosInfo
    assert "SmbiosInfo" in hardware.__all__